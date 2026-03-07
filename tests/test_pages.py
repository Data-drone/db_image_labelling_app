"""
Integration tests for Streamlit pages using st.testing.v1.AppTest.

Note: AppTest cannot test third-party components (streamlit-drawable-canvas),
so canvas interactions are tested at the unit level in test_labeling.py.
"""

import os
import sys

import pytest

# Ensure project root is on the path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestHomePage:
    def test_renders_without_error(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"))
        at.run()
        assert not at.exception, f"App raised exception: {at.exception}"

    def test_has_title(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"))
        at.run()
        titles = at.title
        assert len(titles) >= 1
        assert "CV Dataset Explorer" in titles[0].value

    def test_has_navigation_buttons(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"))
        at.run()
        buttons = at.button
        # Should have "Open Browse Volumes", "Open Dataset Explorer", "Open Labeling"
        assert len(buttons) >= 3

    def test_shows_no_dataset_warning(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"))
        at.run()
        warnings = at.warning
        assert len(warnings) >= 1


class TestBrowseVolumesPage:
    def test_renders_without_error(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "1_📁_Browse_Volumes.py")
        )
        at.run()
        assert not at.exception, f"Page raised exception: {at.exception}"

    def test_shows_local_mode_info(self):
        """When not on Databricks, should show local mode info."""
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "1_📁_Browse_Volumes.py")
        )
        at.run()
        info_messages = at.info
        assert len(info_messages) >= 1

    def test_has_local_path_input_or_info(self):
        """In local mode, should show either a text input or info message."""
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "1_📁_Browse_Volumes.py")
        )
        at.run()
        # In local mode should show info about not being on Databricks
        # text_input might not be exposed via AppTest depending on layout
        has_info = len(at.info) >= 1
        has_input = len(at.text_input) >= 1
        assert has_info or has_input


class TestDatasetExplorerPage:
    def test_renders_without_error(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "2_🖼️_Dataset_Explorer.py")
        )
        at.run()
        # With no datasets, it should show info and stop (not crash)
        assert not at.exception, f"Page raised exception: {at.exception}"

    def test_shows_no_datasets_info(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "2_🖼️_Dataset_Explorer.py")
        )
        at.run()
        info_messages = at.info
        assert len(info_messages) >= 1


class TestSearchPage:
    def test_renders_without_error(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "4_🔍_Search.py")
        )
        at.run()
        assert not at.exception, f"Page raised exception: {at.exception}"

    def test_shows_no_datasets_info(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "4_🔍_Search.py")
        )
        at.run()
        info_messages = at.info
        assert len(info_messages) >= 1


class TestDashboardPage:
    def test_renders_without_error(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "5_📊_Dashboard.py")
        )
        at.run()
        assert not at.exception, f"Page raised exception: {at.exception}"

    def test_shows_no_datasets_info(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "5_📊_Dashboard.py")
        )
        at.run()
        info_messages = at.info
        assert len(info_messages) >= 1


class TestLabelingPage:
    def test_renders_without_error(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "3_🏷️_Labeling.py")
        )
        at.run()
        # With no datasets, should show info and stop
        assert not at.exception, f"Page raised exception: {at.exception}"

    def test_shows_no_datasets_info(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(PROJECT_ROOT, "pages", "3_🏷️_Labeling.py")
        )
        at.run()
        info_messages = at.info
        assert len(info_messages) >= 1
