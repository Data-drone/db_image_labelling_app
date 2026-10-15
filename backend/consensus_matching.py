"""Deterministic spatial matching for blind consensus submissions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from . import masks


def bbox_iou(left: dict | None, right: dict | None) -> float:
    """Return IoU for normalized ``x/y/w/h`` boxes."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return 0.0
    try:
        ax, ay, aw, ah = (float(left[key]) for key in ("x", "y", "w", "h"))
        bx, by, bw, bh = (float(right[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return 0.0
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    intersection = max(0.0, min(ax2, bx2) - max(ax, bx)) * max(
        0.0, min(ay2, by2) - max(ay, by),
    )
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def _mask_parts(mask: dict | None) -> tuple[int, int, list[int]] | None:
    wire = masks.to_wire(mask)
    if not wire:
        return None
    try:
        height, width = int(wire["size"][0]), int(wire["size"][1])
        counts = [int(value) for value in wire["counts"]]
    except (KeyError, TypeError, ValueError):
        return None
    if height <= 0 or width <= 0 or sum(counts) != height * width:
        return None
    return height, width, counts


def mask_iou(left: dict | None, right: dict | None) -> float:
    """Return pixel IoU for compressed or uncompressed COCO RLE masks."""
    left_parts = _mask_parts(left)
    right_parts = _mask_parts(right)
    if not left_parts or not right_parts:
        return 0.0
    lh, lw, left_counts = left_parts
    rh, rw, right_counts = right_parts
    if (lh, lw) != (rh, rw):
        return 0.0
    left_bbox = masks.normalized_bbox(left_counts, lh, lw)
    right_bbox = masks.normalized_bbox(right_counts, rh, rw)
    if bbox_iou(left_bbox, right_bbox) == 0:
        return 0.0
    left_bitmap = masks.bitmap_from_counts(left_counts, lh, lw)
    right_bitmap = masks.bitmap_from_counts(right_counts, rh, rw)
    intersection = int(np.logical_and(left_bitmap, right_bitmap).sum())
    union = int(np.logical_or(left_bitmap, right_bitmap).sum())
    return intersection / union if union else 0.0


@dataclass(frozen=True)
class SpatialNode:
    key: int
    task_id: int
    annotation_id: int | None
    label: str
    ann_type: str
    bbox_json: dict | None
    mask_json: dict | None

    def public_annotation(self) -> dict:
        result = {"label": self.label, "ann_type": self.ann_type}
        if self.bbox_json is not None:
            result["bbox_json"] = self.bbox_json
        if self.mask_json is not None:
            result["mask_json"] = self.mask_json
        return result


def _value(annotation: Any, name: str, default=None):
    if isinstance(annotation, dict):
        return annotation.get(name, default)
    return getattr(annotation, name, default)


def _nodes(submissions: list[dict]) -> list[SpatialNode]:
    result: list[SpatialNode] = []
    for submission in submissions:
        task_id = int(submission["task_id"])
        for annotation in submission.get("annotations", []):
            result.append(SpatialNode(
                key=len(result),
                task_id=task_id,
                annotation_id=_value(annotation, "id"),
                label=str(_value(annotation, "label", "")).strip(),
                ann_type=str(_value(annotation, "ann_type", "")),
                bbox_json=_value(annotation, "bbox_json"),
                mask_json=_value(annotation, "mask_json"),
            ))
    return result


def _stable_node_key(node: SpatialNode) -> tuple[int, int]:
    annotation_id = node.annotation_id if node.annotation_id is not None else node.key
    return node.task_id, int(annotation_id)


def match_spatial_annotations(
    submissions: list[dict],
    *,
    task_type: str,
    required_votes: int,
    quorum: int,
    iou_threshold: float,
    agreement_threshold: float,
) -> dict:
    """Cluster independent shapes and return a JSON-serializable merge result.

    The graph is consumed from strongest to weakest overlap. Components may
    contain at most one annotation from each task and must retain a medoid
    whose IoU with every member is above the configured threshold.
    """
    nodes = _nodes(submissions)
    similarity: Callable[[dict | None, dict | None], float]
    geometry_name: str
    if task_type == "detection":
        similarity = bbox_iou
        geometry_name = "bbox_json"
    elif task_type == "segmentation":
        similarity = mask_iou
        geometry_name = "mask_json"
    else:
        raise ValueError(f"Unsupported spatial task type: {task_type}")

    def node_similarity(left: SpatialNode, right: SpatialNode) -> float:
        return similarity(getattr(left, geometry_name), getattr(right, geometry_name))

    pair_scores: dict[tuple[int, int], float] = {}
    same_label_edges: list[tuple[float, int, int]] = []
    class_conflicts: list[dict] = []
    for index, left in enumerate(nodes):
        for right in nodes[index + 1:]:
            if left.task_id == right.task_id:
                continue
            score = node_similarity(left, right)
            pair_scores[(min(left.key, right.key), max(left.key, right.key))] = score
            if score < iou_threshold:
                continue
            if left.label == right.label:
                same_label_edges.append((score, left.key, right.key))
            else:
                class_conflicts.append({
                    "type": "class_conflict",
                    "task_annotation_ids": [
                        left.annotation_id, right.annotation_id,
                    ],
                    "task_ids": [left.task_id, right.task_id],
                    "labels": [left.label, right.label],
                    "iou": round(score, 6),
                })

    same_label_edges.sort(
        key=lambda row: (
            -row[0],
            _stable_node_key(nodes[row[1]]),
            _stable_node_key(nodes[row[2]]),
        ),
    )
    parent = list(range(len(nodes)))
    members = {node.key: {node.key} for node in nodes}
    tasks = {node.key: {node.task_id} for node in nodes}
    ambiguous_edges: list[dict] = []

    def root(key: int) -> int:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def score(left_key: int, right_key: int) -> float:
        if left_key == right_key:
            return 1.0
        return pair_scores.get(
            (min(left_key, right_key), max(left_key, right_key)),
            0.0,
        )

    def medoid_for(keys: set[int]) -> tuple[int, float, float]:
        ranked = []
        for candidate in keys:
            values = [score(candidate, other) for other in keys if other != candidate]
            mean = sum(values) / len(values) if values else 1.0
            minimum = min(values) if values else 1.0
            ranked.append((
                -mean,
                _stable_node_key(nodes[candidate]),
                candidate,
                mean,
                minimum,
            ))
        _, _, candidate, mean, minimum = min(ranked)
        return candidate, mean, minimum

    for edge_score, left_key, right_key in same_label_edges:
        left_root, right_root = root(left_key), root(right_key)
        if left_root == right_root:
            continue
        combined = members[left_root] | members[right_root]
        reason = None
        if tasks[left_root] & tasks[right_root]:
            reason = "duplicate_task"
        else:
            _, _, minimum = medoid_for(combined)
            if minimum < iou_threshold:
                reason = "transitive_drift"
        if reason:
            ambiguous_edges.append({
                "type": "ambiguous_match",
                "reason": reason,
                "task_annotation_ids": [
                    nodes[left_key].annotation_id,
                    nodes[right_key].annotation_id,
                ],
                "iou": round(edge_score, 6),
            })
            continue
        keep, merge = (
            (left_root, right_root)
            if _stable_node_key(nodes[left_root]) <= _stable_node_key(nodes[right_root])
            else (right_root, left_root)
        )
        parent[merge] = keep
        members[keep] = combined
        tasks[keep] |= tasks[merge]

    clusters: list[dict] = []
    proposed: list[dict] = []
    conflicts = [*class_conflicts, *ambiguous_edges]
    for cluster_index, cluster_keys in enumerate(
        sorted(
            (value for key, value in members.items() if root(key) == key),
            key=lambda value: min(_stable_node_key(nodes[key]) for key in value),
        ),
    ):
        medoid_key, localization_score, _ = medoid_for(cluster_keys)
        medoid = nodes[medoid_key]
        support = len({nodes[key].task_id for key in cluster_keys})
        support_ratio = support / required_votes if required_votes else 0.0
        agreement_score = support_ratio * localization_score
        accepted = support >= quorum and agreement_score >= agreement_threshold
        member_rows = []
        for key in sorted(cluster_keys, key=lambda item: _stable_node_key(nodes[item])):
            member = nodes[key]
            member_rows.append({
                "task_id": member.task_id,
                "task_annotation_id": member.annotation_id,
                "label": member.label,
                "iou_to_medoid": round(score(key, medoid_key), 6),
            })
        cluster = {
            "cluster_id": cluster_index,
            "label": medoid.label,
            "ann_type": medoid.ann_type,
            "members": member_rows,
            "support": support,
            "support_ratio": round(support_ratio, 6),
            "localization_score": round(localization_score, 6),
            "agreement_score": round(agreement_score, 6),
            "medoid_task_id": medoid.task_id,
            "medoid_annotation_id": medoid.annotation_id,
            "accepted": accepted,
        }
        clusters.append(cluster)
        if accepted:
            annotation = medoid.public_annotation()
            annotation["cluster_id"] = cluster_index
            annotation["agreement_score"] = round(agreement_score, 6)
            annotation["support"] = support
            proposed.append(annotation)
        else:
            conflicts.append({
                "type": "unmatched" if support == 1 else "below_quorum",
                "cluster_id": cluster_index,
                "label": medoid.label,
                "support": support,
                "agreement_score": round(agreement_score, 6),
            })

    if not nodes:
        overall_score = 1.0
        vote_count = min(len(submissions), required_votes)
    else:
        overall_score = (
            sum(cluster["agreement_score"] for cluster in clusters if cluster["accepted"])
            / len([cluster for cluster in clusters if cluster["accepted"]])
            if any(cluster["accepted"] for cluster in clusters)
            else 0.0
        )
        vote_count = min(
            (cluster["support"] for cluster in clusters if cluster["accepted"]),
            default=0,
        )
    return {
        "final_annotations": proposed,
        "agreement_score": round(overall_score, 6),
        "vote_count": vote_count,
        "requires_review": bool(conflicts),
        "match_report": {
            "task_type": task_type,
            "required_votes": required_votes,
            "quorum": quorum,
            "iou_threshold": iou_threshold,
            "agreement_threshold": agreement_threshold,
            "clusters": clusters,
            "conflicts": conflicts,
        },
    }
