"""
Tests for utils/volumes.py — directory listing, image counting, local fallback.
"""

import os
import tempfile

import pytest

from utils.volumes import (
    list_directory,
    is_media_file,
    count_images_in,
    get_local_folders,
)


class TestIsMediaFile:
    @pytest.mark.parametrize("name,expected", [
        ("photo.jpg", True),
        ("photo.JPEG", True),
        ("image.png", True),
        ("image.bmp", True),
        ("image.webp", True),
        ("video.mp4", True),
        ("video.avi", True),
        ("readme.txt", False),
        ("data.csv", False),
        ("script.py", False),
        (".hidden", False),
    ])
    def test_extensions(self, name, expected):
        assert is_media_file(name) == expected


class TestListDirectory:
    def test_lists_folders_and_images(self, tmp_image_dir):
        # Create a subfolder
        subdir = os.path.join(tmp_image_dir, "subdir")
        os.makedirs(subdir)

        folders, files = list_directory(tmp_image_dir)
        assert "subdir" in folders
        assert any(f.endswith(".jpg") for f in files)
        assert "readme.txt" not in files  # non-media excluded

    def test_empty_directory(self):
        tmpdir = tempfile.mkdtemp()
        folders, files = list_directory(tmpdir)
        assert folders == []
        assert files == []
        os.rmdir(tmpdir)

    def test_nonexistent_directory(self):
        folders, files = list_directory("/nonexistent/path")
        assert folders == []
        assert files == []


class TestCountImagesIn:
    def test_counts_images(self, tmp_image_dir):
        count = count_images_in(tmp_image_dir)
        assert count == 5

    def test_counts_recursively(self, tmp_image_dir):
        subdir = os.path.join(tmp_image_dir, "nested")
        os.makedirs(subdir)
        from PIL import Image
        img = Image.new("RGB", (10, 10))
        img.save(os.path.join(subdir, "nested.png"))

        count = count_images_in(tmp_image_dir)
        assert count == 6  # 5 original + 1 nested

    def test_nonexistent_returns_zero(self):
        assert count_images_in("/nonexistent") == 0

    def test_ignores_non_images(self, tmp_image_dir):
        # readme.txt already exists, verify it's not counted
        count = count_images_in(tmp_image_dir)
        assert count == 5


class TestGetLocalFolders:
    def test_returns_list(self):
        folders = get_local_folders(tempfile.gettempdir())
        assert isinstance(folders, list)

    def test_only_directories(self):
        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, "folder_a"))
        os.makedirs(os.path.join(tmpdir, "folder_b"))
        with open(os.path.join(tmpdir, "file.txt"), "w") as f:
            f.write("test")

        folders = get_local_folders(tmpdir)
        assert "folder_a" in folders
        assert "folder_b" in folders
        assert "file.txt" not in folders

    def test_sorted(self):
        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, "zzz"))
        os.makedirs(os.path.join(tmpdir, "aaa"))
        folders = get_local_folders(tmpdir)
        assert folders == sorted(folders)

    def test_nonexistent_returns_empty(self):
        folders = get_local_folders("/nonexistent/path")
        assert folders == []
