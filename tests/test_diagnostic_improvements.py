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


    # -----------------------------------------------------------------------
    # Texture_Target Regression and Control Tests
    # -----------------------------------------------------------------------
    def test_09_texture_target_disconnected_image_color(self):
        """Reproduce Texture_Target scenario: Image Texture exists and Principled BSDF is connected
        to Material Output, but Image Texture Color is disconnected from Principled BSDF Base Color.
        Assert that the tool reports both facts accurately from evidence."""
        # Create Texture_Target object
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
        target_obj = bpy.context.active_object
        target_obj.name = "Texture_Target"

        mat = bpy.data.materials.new("TargetMat")
        mat.use_nodes = True
        target_obj.data.materials.append(mat)

        nodes = mat.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        out_node = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')

        # Add Image Texture node with a valid image data-block
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.name = "Image Texture"
        img = bpy.data.images.new("target_texture.png", width=128, height=128)
        tex_node.image = img

        # Ensure Principled BSDF is connected to Material Output Surface
        # but Image Texture Color is DISCONNECTED from Base Color
        if not out_node.inputs["Surface"].is_linked:
            mat.node_tree.links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])

        res = get_material_details("Texture_Target")
        self.assertEqual(res["object_name"], "Texture_Target")
        self.assertEqual(res["material_slots_count"], 1)

        mat_entry = res["materials"][0]
        # 1. Shader connected to Material Output
        self.assertTrue(mat_entry["surface_connected"])
        self.assertEqual(mat_entry["surface_shader"]["node_name"], bsdf.name)

        # 2. Base Color is NOT linked
        bc_input = mat_entry["principled_bsdf"]["inputs"]["base_color"]
        self.assertFalse(bc_input["is_linked"])
        self.assertIsNone(bc_input["linked_from"])
        self.assertIsNotNone(bc_input["default_value"])

        # 3. Image Texture Color output is NOT connected
        tex_entry = mat_entry["image_textures"][0]
        self.assertEqual(tex_entry["node_name"], "Image Texture")
        self.assertEqual(tex_entry["image_name"], "target_texture.png")
        self.assertFalse(tex_entry["color_output_connected"])
        self.assertEqual(len(tex_entry["color_destinations"]), 0)

        # 4. Overall helper flag
        self.assertFalse(mat_entry["image_texture_connected_to_base_color"])

        # 5. Node links check: contains BSDF -> Output, does NOT contain Tex -> BSDF
        links = mat_entry["node_links"]
        self.assertTrue(any(l["from_node"] == bsdf.name and l["to_node"] == out_node.name for l in links))
        self.assertFalse(any(l["from_node"] == tex_node.name and l["to_node"] == bsdf.name for l in links))

        # 6. Observations / diagnostic summary report the exact facts
        self.assertTrue(any("Image Texture" in obs and "Color output is NOT connected" in obs for obs in mat_entry["observations"]))
        self.assertIn("Color output is NOT connected to Principled BSDF Base Color", res["diagnostic_summary"])

        print("✅ Test 9 Passed: Texture_Target disconnected Image Texture Color accurately reported from tool evidence.")

    def test_10_texture_target_connected_control(self):
        """Control test: Image Texture Color is connected to Principled BSDF Base Color.
        Verify that the tool reports the connection and destination sockets accurately."""
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
        target_obj = bpy.context.active_object
        target_obj.name = "Texture_Target_Connected"

        mat = bpy.data.materials.new("ConnectedMat")
        mat.use_nodes = True
        target_obj.data.materials.append(mat)

        nodes = mat.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        out_node = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.name = "Image Texture"
        img = bpy.data.images.new("connected_texture.png", width=128, height=128)
        tex_node.image = img

        # Connect both: Surface -> Output, and Image Texture Color -> Base Color
        mat.node_tree.links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
        mat.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])

        res = get_material_details("Texture_Target_Connected")
        mat_entry = res["materials"][0]

        # 1. Surface connected
        self.assertTrue(mat_entry["surface_connected"])

        # 2. Base Color IS linked from Image Texture Color
        bc_input = mat_entry["principled_bsdf"]["inputs"]["base_color"]
        self.assertTrue(bc_input["is_linked"])
        self.assertEqual(bc_input["linked_from"]["node_name"], tex_node.name)
        self.assertEqual(bc_input["linked_from"]["socket_name"], "Color")

        # 3. Image Texture Color IS connected to Base Color
        tex_entry = mat_entry["image_textures"][0]
        self.assertTrue(tex_entry["color_output_connected"])
        self.assertEqual(len(tex_entry["color_destinations"]), 1)
        self.assertEqual(tex_entry["color_destinations"][0]["to_node"], bsdf.name)
        self.assertEqual(tex_entry["color_destinations"][0]["to_socket"], "Base Color")

        # 4. Helper flag is True
        self.assertTrue(mat_entry["image_texture_connected_to_base_color"])

        # 5. Node links check: contains both links
        links = mat_entry["node_links"]
        self.assertTrue(any(l["from_node"] == tex_node.name and l["to_node"] == bsdf.name and l["to_socket"] == "Base Color" for l in links))
        self.assertTrue(any(l["from_node"] == bsdf.name and l["to_node"] == out_node.name for l in links))

        print("✅ Test 10 Passed: Control test verifies connected Image Texture Color is accurately reported.")

    def test_11_disconnected_texture_distinguishes_no_pixel_data(self):
        """Verify that when an Image Texture is disconnected from Base Color AND the image
        has no pixel data, the diagnosis distinguishes the confirmed direct cause (missing link)
        from the conditional outcome (missing image data upon reconnection)."""
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
        target_obj = bpy.context.active_object
        target_obj.name = "Texture_NoData"

        mat = bpy.data.materials.new("NoDataMat")
        mat.use_nodes = True
        target_obj.data.materials.append(mat)

        nodes = mat.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        out_node = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.name = "Image Texture"
        # Create an image that has no pixel data (source='FILE' with no loaded file)
        img = bpy.data.images.new("Empty_Texture.png", width=512, height=512)
        img.source = 'FILE'
        tex_node.image = img

        # Connect Surface -> Output, but leave Image Texture Color DISCONNECTED from Base Color
        mat.node_tree.links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])

        res = get_material_details("Texture_NoData")
        mat_entry = res["materials"][0]
        tex_entry = mat_entry["image_textures"][0]

        # 1. Image Texture status reflects missing pixel data
        self.assertFalse(tex_entry["has_data"])
        self.assertIn("NO_DATA", tex_entry["status"])

        # 2. Confirmed direct cause is reported
        self.assertFalse(mat_entry["image_texture_connected_to_base_color"])
        self.assertTrue(any("Confirmed cause" in obs and "Color output is NOT connected" in obs for obs in mat_entry["observations"]))

        # 3. Secondary conditional finding is reported separately
        self.assertTrue(any("Secondary finding (conditional)" in obs and "no pixel data loaded" in obs for obs in mat_entry["observations"]))
        self.assertTrue(any("Secondary finding (conditional)" in obs and "may not display a meaningful texture" in obs for obs in mat_entry["observations"]))

        # 4. Diagnostic summary contains both
        self.assertIn("Confirmed cause", res["diagnostic_summary"])
        self.assertIn("Secondary finding (conditional)", res["diagnostic_summary"])

        print("✅ Test 11 Passed: Disconnected texture and missing pixel data distinguished as confirmed vs conditional.")


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDiagnosticImprovements)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    run_tests()
