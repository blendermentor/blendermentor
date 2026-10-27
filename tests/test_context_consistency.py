# SPDX-License-Identifier: GPL-3.0-or-later
# Test suite for BlenderMentor context consistency across interaction modes,
# object selections, and worker threads.
#
# Run inside Blender using:
#   blender --background --python tests/test_context_consistency.py

import sys
import os
import unittest
import threading

# Ensure the addon root is importable
addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)

import bpy
import bmesh
from addon.scene_reader import (
    get_basic_context,
    get_selection_info,
    get_mesh_geometry_summary,
    get_object_details,
    get_active_tool_info,
    get_viewport_state,
    _resolve_interaction_mode,
    _get_active_object,
    _get_selected_objects,
)


class TestContextConsistency(unittest.TestCase):
    """Test suite verifying context consistency across Object and Edit modes."""

    def setUp(self):
        """Reset scene with factory settings."""
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
        self.cube = bpy.context.active_object
        self.cube.name = "Cube"
        bpy.context.view_layer.objects.active = self.cube
        self.cube.select_set(True)

    def tearDown(self):
        """Ensure clean state."""
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

    # -----------------------------------------------------------------------
    # Scenario 1: Edit Mode reported correctly while vertices are moved
    # -----------------------------------------------------------------------
    def test_01_edit_mode_reported_while_moving_vertices(self):
        """Verify Edit Mode is reported consistently while vertices are edited without exiting Edit Mode."""
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(self.cube.data)
        for v in bm.verts:
            v.co.x += 2.5
        bmesh.update_edit_mesh(self.cube.data)

        # 1. Main thread checks
        basic = get_basic_context()
        self.assertEqual(basic["mode"], "EDIT_MESH")
        self.assertEqual(basic["active_object"], "Cube")

        sel = get_selection_info()
        self.assertEqual(sel["mode"], "EDIT_MESH")
        self.assertEqual(sel["active_object_mode"], "EDIT")
        self.assertTrue(sel["is_edit_mode"])

        geom = get_mesh_geometry_summary("Cube")
        self.assertEqual(geom["mode"], "EDIT_MESH")
        self.assertTrue(geom["is_edit_mode"])

        obj_det = get_object_details("Cube")
        self.assertEqual(obj_det["mode"], "EDIT")
        self.assertTrue(obj_det["is_edit_mode"])

        # 2. Worker thread checks (simulating background AI tool execution)
        bg_results = {}

        def bg_worker():
            bg_results["basic"] = get_basic_context()
            bg_results["sel"] = get_selection_info()
            bg_results["geom"] = get_mesh_geometry_summary("Cube")
            bg_results["obj_det"] = get_object_details("Cube")
            bg_results["tool"] = get_active_tool_info()

        t = threading.Thread(target=bg_worker)
        t.start()
        t.join()

        self.assertEqual(bg_results["sel"]["mode"], "EDIT_MESH")
        self.assertEqual(bg_results["sel"]["active_object_mode"], "EDIT")
        self.assertTrue(bg_results["sel"]["is_edit_mode"])

        self.assertEqual(bg_results["geom"]["mode"], "EDIT_MESH")
        self.assertTrue(bg_results["geom"]["is_edit_mode"])

        self.assertEqual(bg_results["obj_det"]["mode"], "EDIT")
        self.assertTrue(bg_results["obj_det"]["is_edit_mode"])

        self.assertEqual(bg_results["tool"]["mode"], "EDIT_MESH")

        print("✅ Scenario 1 Passed: Edit Mode consistently reported across all tools in main and worker threads.")

    # -----------------------------------------------------------------------
    # Scenario 2: Object Mode reported correctly after leaving Edit Mode
    # -----------------------------------------------------------------------
    def test_02_object_mode_reported_after_leaving_edit_mode(self):
        """Verify Object Mode is reported correctly across all tools after leaving Edit Mode."""
        # Enter then leave Edit Mode
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.mode_set(mode='OBJECT')

        basic = get_basic_context()
        self.assertEqual(basic["mode"], "OBJECT")

        sel = get_selection_info()
        self.assertEqual(sel["mode"], "OBJECT")
        self.assertEqual(sel["active_object_mode"], "OBJECT")
        self.assertFalse(sel["is_edit_mode"])
        self.assertNotIn("sub_element_selection", sel)

        geom = get_mesh_geometry_summary("Cube")
        self.assertEqual(geom["mode"], "OBJECT")
        self.assertFalse(geom["is_edit_mode"])

        obj_det = get_object_details("Cube")
        self.assertEqual(obj_det["mode"], "OBJECT")
        self.assertFalse(obj_det["is_edit_mode"])

        # Background thread verification
        bg_results = {}

        def bg_worker():
            bg_results["sel"] = get_selection_info()
            bg_results["geom"] = get_mesh_geometry_summary("Cube")
            bg_results["obj_det"] = get_object_details("Cube")

        t = threading.Thread(target=bg_worker)
        t.start()
        t.join()

        self.assertEqual(bg_results["sel"]["mode"], "OBJECT")
        self.assertFalse(bg_results["sel"]["is_edit_mode"])
        self.assertEqual(bg_results["geom"]["mode"], "OBJECT")
        self.assertFalse(bg_results["geom"]["is_edit_mode"])
        self.assertEqual(bg_results["obj_det"]["mode"], "OBJECT")
        self.assertFalse(bg_results["obj_det"]["is_edit_mode"])

        print("✅ Scenario 2 Passed: Object Mode consistently reported across all tools after leaving Edit Mode.")

    # -----------------------------------------------------------------------
    # Scenario 3: Repeated queries after switching modes return fresh state
    # -----------------------------------------------------------------------
    def test_03_repeated_queries_after_mode_switching(self):
        """Verify repeated queries alternate states cleanly without caching or stale data."""
        for cycle in range(3):
            # Switch to EDIT
            bpy.ops.object.mode_set(mode='EDIT')
            self.assertEqual(get_selection_info()["mode"], "EDIT_MESH")
            self.assertTrue(get_selection_info()["is_edit_mode"])
            self.assertTrue(get_mesh_geometry_summary("Cube")["is_edit_mode"])

            # Switch to OBJECT
            bpy.ops.object.mode_set(mode='OBJECT')
            self.assertEqual(get_selection_info()["mode"], "OBJECT")
            self.assertFalse(get_selection_info()["is_edit_mode"])
            self.assertFalse(get_mesh_geometry_summary("Cube")["is_edit_mode"])

        print("✅ Scenario 3 Passed: Repeated mode transitions return fresh, uncached state every time.")

    # -----------------------------------------------------------------------
    # Scenario 4: Active object mode distinguished from other selected objects
    # -----------------------------------------------------------------------
    def test_04_active_object_mode_distinguished_from_selected_objects(self):
        """Verify active object mode is distinguished from individual modes of selected objects."""
        # Add a Point Light
        bpy.ops.object.light_add(type='POINT', location=(5, 5, 5))
        light = bpy.context.active_object
        light.name = "PointLight"

        # Select both Cube and Light, but make Cube active
        self.cube.select_set(True)
        light.select_set(True)
        bpy.context.view_layer.objects.active = self.cube

        # Enter Edit Mode on Cube
        bpy.ops.object.mode_set(mode='EDIT')

        # Cube is in EDIT mode, Light is in OBJECT mode
        sel = get_selection_info()
        self.assertEqual(sel["active_object"], "Cube")
        self.assertEqual(sel["mode"], "EDIT_MESH")
        self.assertEqual(sel["active_object_mode"], "EDIT")
        self.assertTrue(sel["is_edit_mode"])

        # Check selected objects list
        cube_entry = next(o for o in sel["selected_objects"] if o["name"] == "Cube")
        light_entry = next(o for o in sel["selected_objects"] if o["name"] == "PointLight")

        self.assertEqual(cube_entry["mode"], "EDIT")
        self.assertEqual(light_entry["mode"], "OBJECT")

        print("✅ Scenario 4 Passed: Active object mode cleanly distinguished from individual selected objects.")

    # -----------------------------------------------------------------------
    # Scenario 5: Scene geometry and mode information consistent in same snapshot
    # -----------------------------------------------------------------------
    def test_05_scene_geometry_and_mode_consistent_within_snapshot(self):
        """Verify geometry counts and modes match across basic context, geometry summary, and object details."""
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(self.cube.data)

        # Subdivide cube in edit mode: adds vertices and faces
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
        bmesh.update_edit_mesh(self.cube.data)

        # Query all three context sources
        basic = get_basic_context()
        geom = get_mesh_geometry_summary("Cube")
        obj_det = get_object_details("Cube")

        # 1. Verify Mode consistency
        self.assertEqual(basic["mode"], "EDIT_MESH")
        self.assertEqual(geom["mode"], "EDIT_MESH")
        self.assertTrue(geom["is_edit_mode"])
        self.assertTrue(obj_det["is_edit_mode"])

        # 2. Verify Geometry count consistency
        basic_verts = basic["active_object_details"]["vertex_count"]
        geom_verts = geom["vertex_count"]
        det_verts = obj_det["vertex_count"]

        self.assertGreater(basic_verts, 8, "Expected subdivided cube to have more than 8 vertices")
        self.assertEqual(basic_verts, geom_verts)
        self.assertEqual(geom_verts, det_verts)

        basic_faces = basic["active_object_details"]["face_count"]
        geom_faces = geom["face_count"]
        det_faces = obj_det["face_count"]

        self.assertGreater(basic_faces, 6, "Expected subdivided cube to have more than 6 faces")
        self.assertEqual(basic_faces, geom_faces)
        self.assertEqual(geom_faces, det_faces)

        print("✅ Scenario 5 Passed: Geometry and mode are 100% consistent across all tools within the snapshot.")

    # -----------------------------------------------------------------------
    # Scenario 6: Background thread viewport state fallback
    # -----------------------------------------------------------------------
    def test_06_background_thread_viewport_state(self):
        """Verify get_viewport_state succeeds inside worker threads without returning None."""
        bg_vp = {}

        def bg_worker():
            bg_vp.update(get_viewport_state())

        t = threading.Thread(target=bg_worker)
        t.start()
        t.join()

        self.assertIsNotNone(bg_vp.get("shading_type"))
        self.assertIsNotNone(bg_vp.get("show_overlays"))

        print("✅ Scenario 6 Passed: Viewport state cleanly retrieved from background worker thread.")


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestContextConsistency)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    run_tests()
