export function galleryTotalPages(total, pageSize) {
  if (!Number.isFinite(total) || !Number.isFinite(pageSize) || pageSize <= 0) {
    return 0;
  }
  return Math.max(0, Math.ceil(Math.max(0, total) / pageSize));
}

export function clampGalleryPage(page, total, pageSize) {
  const pages = galleryTotalPages(total, pageSize);
  const lastPage = Math.max(0, pages - 1);
  return Math.min(Math.max(0, page), lastPage);
}

export function shouldShowGalleryPagination({
  similarMode,
  duplicateMode,
  outlierMode,
  viewMode,
  totalPages,
}) {
  return (
    !similarMode
    && !duplicateMode
    && !outlierMode
    && viewMode === 'gallery'
    && totalPages > 1
  );
}

export function buildGalleryParams({
  page,
  pageSize,
  status,
  label,
  labeler,
  filename,
}) {
  const params = { page, page_size: pageSize };
  if (status) params.status = status;
  if (label) params.label = label;
  if (labeler) params.labeler = labeler;
  if (filename) params.filename = filename;
  return params;
}

export function cancelDebounced(ref) {
  if (!ref.current) return;
  clearTimeout(ref.current);
  ref.current = null;
}
