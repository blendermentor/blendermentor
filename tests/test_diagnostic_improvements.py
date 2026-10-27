# SPDX-License-Identifier: GPL-3.0-or-later
# Test suite for BlenderMentor diagnostic pipeline improvements:
# 1. Edit Mode geometry freshness and local/world bounding boxes
# 2. Material and shader inspection (empty slots, disconnected outputs, missing images)
# 3. Edit Mode mesh sub-element selection inspection (zero selected elements warning)
#
# Run inside Blender using:
#   blender --background --python tests/test_diagnostic_improvements.py

import sys
import os
import unittest

# Ensure the addon root is importable
addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)

import bpy
import bmesh
from addon.scene_reader import (
    get_mesh_geometry_summary,
    get_material_details,
    get_selection_info,
    execute_tool,
    TOOL_REGISTRY,
)


class TestDiagnosticImprovements(unittest.TestCase):
    """Test suite for the three diagnostic pipeline improvements."""

    def setUp(self):
        """Create a clean test scene with factory settings."""
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
    # Improvement 1: Edit Mode geometry freshness & Local vs World Bounding Boxes
    # -----------------------------------------------------------------------
    def test_01_edit_mode_geometry_freshness(self):
        """Verify that get_mesh_geometry_summary reads fresh vertex edits while remaining in Edit Mode."""
        # Enter Edit Mode
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(self.cube.data)

        # Translate all vertices by +5 on X while in Edit Mode
        for v in bm.verts:
            v.co.x += 5.0
        bmesh.update_edit_mesh(self.cube.data)

        # Call get_mesh_geometry_summary directly while in Edit Mode
        summary = get_mesh_geometry_summary("Cube")

        self.assertTrue(summary.get("is_edit_mode"), "Expected is_edit_mode to be True")
        self.assertEqual(summary["vertex_count"], 8)

        # Cube of size 2 originally ranged [-1, 1]. Shifted +5, X is now [4, 6]
        self.assertAlmostEqual(summary["bounding_box_local_min"][0], 4.0, places=4)
        self.assertAlmostEqual(summary["bounding_box_local_max"][0], 6.0, places=4)
        self.assertAlmostEqual(summary["centroid_local"][0], 5.0, places=4)

        # Legacy bounding_box_min / max compatibility
        self.assertAlmostEqual(summary["bounding_box_min"][0], 4.0, places=4)
        self.assertAlmostEqual(summary["bounding_box_max"][0], 6.0, places=4)

        print("✅ Test 1 Passed: Fresh Edit Mode geometry correctly inspected without switching to Object Mode.")

    def test_02_local_vs_world_bounding_boxes(self):
        """Verify that local and world bounding boxes are clearly separated and correctly calculated."""
        # Set transforms on Cube: loc=(10, 20, 30), scale=(2, 3, 4)
        self.cube.location = (10.0, 20.0, 30.0)
        self.cube.scale = (2.0, 3.0, 4.0)
        bpy.context.view_layer.update()

        summary = get_mesh_geometry_summary("Cube")

        # Local bounds for size=2 cube: [-1, 1] on each axis
        self.assertEqual(summary["bounding_box_local_min"], [-1.0, -1.0, -1.0])
        self.assertEqual(summary["bounding_box_local_max"], [1.0, 1.0, 1.0])
        self.assertEqual(summary["dimensions_local"], [2.0, 2.0, 2.0])

        # World bounds: scaled and translated
        # X: 10 +/- (1 * 2) -> [8, 12]
        # Y: 20 +/- (1 * 3) -> [17, 23]
        # Z: 30 +/- (1 * 4) -> [26, 34]
        self.assertAlmostEqual(summary["bounding_box_world_min"][0], 8.0, places=4)
        self.assertAlmostEqual(summary["bounding_box_world_max"][0], 12.0, places=4)
        self.assertAlmostEqual(summary["bounding_box_world_min"][1], 17.0, places=4)
        self.assertAlmostEqual(summary["bounding_box_world_max"][1], 23.0, places=4)
        self.assertAlmostEqual(summary["bounding_box_world_min"][2], 26.0, places=4)
        self.assertAlmostEqual(summary["bounding_box_world_max"][2], 34.0, places=4)

        self.assertEqual(summary["dimensions_world"], [4.0, 6.0, 8.0])
        self.assertEqual(summary["centroid_world"], [10.0, 20.0, 30.0])

        print("✅ Test 2 Passed: Local and World bounding boxes clearly distinguished and accurate.")

    # -----------------------------------------------------------------------
    # Improvement 2: Material and Shader Inspection
    # -----------------------------------------------------------------------
    def test_03_material_empty_slot_detection(self):
        """Verify detection of empty material slots and face assignments."""
        # Add a valid material to slot 0
        mat = bpy.data.materials.new("ValidMat")
        self.cube.data.materials.append(mat)

        # Add an empty slot (slot 1)
        self.cube.data.materials.append(None)

        res = get_material_details("Cube")
        self.assertEqual(res["material_slots_count"], 2)
        self.assertEqual(res["material_slots"][0]["material_name"], "ValidMat")
        self.assertIsNone(res["material_slots"][1]["material_name"])

        # Check detected issues
        self.assertTrue(any("Material slot 1 is empty" in issue for issue in res["detected_issues"]))
        self.assertIn("Material slot 1 is empty", res["diagnostic_summary"])

        print("✅ Test 3 Passed: Empty material slot correctly detected and reported.")

    def test_04_material_disconnected_surface_socket(self):
        """Verify detection of disconnected Material Output surface socket (black/unshaded render)."""
        mat = bpy.data.materials.new("DisconnMat")
        # Ensure node tree exists
        if mat.node_tree is None:
            mat.use_nodes = True
        self.cube.data.materials.append(mat)

        # Find Material Output and remove links into 'Surface'
        out_node = next(n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL')
        surf_sock = out_node.inputs["Surface"]
        for link in list(surf_sock.links):
            mat.node_tree.links.remove(link)

        res = get_material_details("Cube")
        mat_entry = next(m for m in res["materials"] if m["material_name"] == "DisconnMat")
        self.assertFalse(mat_entry["surface_connected"])

        # Check detected issues
        self.assertTrue(any("NO shader connected to the 'Surface' socket" in issue for issue in res["detected_issues"]))
        self.assertIn("pitch black or unshaded", res["diagnostic_summary"])

        print("✅ Test 4 Passed: Disconnected Material Output surface socket correctly diagnosed.")

    def test_05_material_missing_image_texture(self):
        """Verify detection of missing image texture files (renders pink/magenta in Blender)."""
        mat = bpy.data.materials.new("MissingTexMat")
        if mat.node_tree is None:
            mat.use_nodes = True
        self.cube.data.materials.append(mat)

        # Add an Image Texture node pointing to a non-existent file
        tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        img = bpy.data.images.new("GhostTexture.png", width=64, height=64)
        img.filepath = "//textures/non_existent_image_404.png"
        img.source = 'FILE'
        tex_node.image = img

        res = get_material_details("Cube")
        mat_entry = next(m for m in res["materials"] if m["material_name"] == "MissingTexMat")
        self.assertIn("image_textures", mat_entry)
        self.assertEqual(len(mat_entry["image_textures"]), 1)

        tex_item = mat_entry["image_textures"][0]
        self.assertTrue(tex_item["file_missing"])
        self.assertIn("FILE MISSING", tex_item["status"])

        # Check detected issues
        self.assertTrue(any("missing file" in issue for issue in res["detected_issues"]))
        self.assertIn("magenta/pink", res["diagnostic_summary"])

        print("✅ Test 5 Passed: Missing image texture file correctly detected and diagnosed.")

    # -----------------------------------------------------------------------
    # Improvement 3: Edit Mode Mesh Sub-Element Selection
    # -----------------------------------------------------------------------
    def test_06_edit_mode_selection_zero_elements(self):
        """Verify that Edit Mode with 0 selected elements flags has_sub_element_selection=False and provides warning."""
        # Enter Edit Mode
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='DESELECT')

        # In Blender, set selection mode to VERTEX
        bpy.context.tool_settings.mesh_select_mode = (True, False, False)

        sel_info = get_selection_info()
        self.assertIn("sub_element_selection", sel_info)
        sub_sel = sel_info["sub_element_selection"]

        self.assertIn("VERTEX", sub_sel["mesh_select_mode"])
        self.assertEqual(sub_sel["selected_vertices"], 0)
        self.assertEqual(sub_sel["total_vertices"], 8)
        self.assertFalse(sub_sel["has_sub_element_selection"])

        # Check warning
        self.assertIn("selection_warning", sel_info)
        self.assertIn("NO mesh elements (vertices/edges/faces) are currently selected", sel_info["selection_warning"])

        print("✅ Test 6 Passed: Edit Mode zero selection correctly detected with diagnostic warning.")

    def test_07_edit_mode_selection_active_elements(self):
        """Verify that selected mesh elements in Edit Mode are accurately counted and reported."""
        # Enter Edit Mode
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(self.cube.data)
        bm.verts.ensure_lookup_table()

        # Select exactly 2 vertices
        for v in bm.verts:
            v.select = False
        bm.verts[0].select = True
        bm.verts[1].select = True
        bmesh.update_edit_mesh(self.cube.data)

        sel_info = get_selection_info()
        self.assertIn("sub_element_selection", sel_info)
        sub_sel = sel_info["sub_element_selection"]

        self.assertEqual(sub_sel["selected_vertices"], 2)
        self.assertTrue(sub_sel["has_sub_element_selection"])
        self.assertNotIn("selection_warning", sel_info)

        print("✅ Test 7 Passed: Active Edit Mode sub-element selection accurately inspected.")

    # -----------------------------------------------------------------------
    # Tool Registry & Execution Integration
    # -----------------------------------------------------------------------
    def test_08_tool_registry_integration(self):
        """Verify that get_material_details is registered and invokable via execute_tool."""
        self.assertIn("get_material_details", TOOL_REGISTRY)

        # Invoke via execute_tool
        result = execute_tool("get_material_details", {"object_name": "Cube"})
        self.assertEqual(result["object_name"], "Cube")
        self.assertIn("diagnostic_summary", result)

        print("✅ Test 8 Passed: get_material_details registered in TOOL_REGISTRY and cleanly invokable.")


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDiagnosticImprovements)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    run_tests()
