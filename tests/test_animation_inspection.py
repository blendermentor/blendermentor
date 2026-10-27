# SPDX-License-Identifier: GPL-3.0-or-later
# Test suite for BlenderMentor animation inspection and diagnostic capabilities.
#
# Run inside Blender using:
#   blender --background --python tests/test_animation_inspection.py

import sys
import os
import unittest

# Ensure the addon root is importable
addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)

import bpy
from addon.scene_reader import (
    get_animation_details,
    get_object_details,
    _get_object_fcurves,
    _extract_fcurves_from_action,
)


class TestAnimationInspection(unittest.TestCase):
    """Test animation data retrieval across legacy and layered/slotted Blender actions."""

    def setUp(self):
        """Create a fresh test scene with a single clean Cube."""
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        self.cube = bpy.context.active_object
        self.cube.name = "Cube"

    def tearDown(self):
        """Clean up scene objects and data blocks."""
        if hasattr(self, "cube") and self.cube and self.cube.animation_data:
            self.cube.animation_data_clear()

    # -----------------------------------------------------------------------
    # Scenario 1: Constant interpolation produces stepped motion
    # -----------------------------------------------------------------------
    def test_01_constant_interpolation(self):
        """Verify that CONSTANT interpolation is retrieved from keyframes and diagnosed."""
        self.cube.location = (0, 0, 0)
        self.cube.keyframe_insert(data_path="location", frame=1)
        self.cube.location = (2, 0, 0)
        self.cube.keyframe_insert(data_path="location", frame=20)

        # Set all location F-curves to CONSTANT interpolation
        fcurves = _get_object_fcurves(self.cube, "location")
        self.assertGreater(len(fcurves), 0, "Failed to retrieve location F-curves")
        for fc in fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = 'CONSTANT'

        # Test get_animation_details
        result = get_animation_details("Cube", data_path="location")
        self.assertTrue(result["has_animation_data"])
        self.assertEqual(result["object_name"], "Cube")
        self.assertIn("location", result["animated_properties"])
        self.assertEqual(result["fcurves_inspected"], 3)

        # Inspect Location X channel
        fc_x = next(fc for fc in result["fcurves"] if fc["channel"] == "Location X")
        self.assertEqual(len(fc_x["keyframes"]), 2)
        kp1, kp2 = fc_x["keyframes"][0], fc_x["keyframes"][1]

        self.assertEqual(kp1["frame"], 1.0)
        self.assertEqual(kp1["value"], 0.0)
        self.assertEqual(kp1["interpolation"], "CONSTANT")

        self.assertEqual(kp2["frame"], 20.0)
        self.assertEqual(kp2["value"], 2.0)
        self.assertEqual(kp2["interpolation"], "CONSTANT")

        # Verify evidence-based diagnostic summary
        summary = result["diagnostic_summary"]
        self.assertIn("CONSTANT interpolation", summary)
        self.assertIn("stepped", summary.lower())

        # Also verify that get_object_details includes high-level animation summary
        obj_details = get_object_details("Cube")
        self.assertTrue(obj_details["has_animation_data"])
        self.assertIn("CONSTANT", obj_details["animation"]["interpolations_used"])
        self.assertIn("location", obj_details["animation"]["animated_properties"])
        print("✅ Scenario 1 Passed: Constant interpolation correctly retrieved and diagnosed.")

    # -----------------------------------------------------------------------
    # Scenario 2: Linear interpolation produces constant-speed motion
    # -----------------------------------------------------------------------
    def test_02_linear_interpolation(self):
        """Verify that LINEAR interpolation is retrieved and diagnosed."""
        self.cube.location = (0, 0, 0)
        self.cube.keyframe_insert(data_path="location", frame=1)
        self.cube.location = (5, 0, 0)
        self.cube.keyframe_insert(data_path="location", frame=20)

        # Set location F-curves to LINEAR
        fcurves = _get_object_fcurves(self.cube, "location")
        for fc in fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'

        result = get_animation_details("Cube", data_path="location")
        self.assertTrue(result["has_animation_data"])

        fc_x = next(fc for fc in result["fcurves"] if fc["channel"] == "Location X")
        for kp in fc_x["keyframes"]:
            self.assertEqual(kp["interpolation"], "LINEAR")

        summary = result["diagnostic_summary"]
        self.assertIn("LINEAR interpolation", summary)
        self.assertIn("constant speed", summary.lower())
        print("✅ Scenario 2 Passed: Linear interpolation correctly retrieved and diagnosed.")

    # -----------------------------------------------------------------------
    # Scenario 3: Bezier interpolation with configured handles
    # -----------------------------------------------------------------------
    def test_03_bezier_interpolation_with_handles(self):
        """Verify that BEZIER interpolation and handle data are retrieved."""
        self.cube.location = (0, 0, 0)
        self.cube.keyframe_insert(data_path="location", frame=1)
        self.cube.location = (10, 0, 0)
        self.cube.keyframe_insert(data_path="location", frame=30)

        # By default Blender inserts BEZIER keyframes
        fcurves = _get_object_fcurves(self.cube, "location")
        for fc in fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = 'BEZIER'

        result = get_animation_details("Cube", data_path="location")
        self.assertTrue(result["has_animation_data"])

        fc_x = next(fc for fc in result["fcurves"] if fc["channel"] == "Location X")
        self.assertEqual(len(fc_x["keyframes"]), 2)
        kp1 = fc_x["keyframes"][0]

        self.assertEqual(kp1["interpolation"], "BEZIER")
        self.assertIn("handle_left", kp1)
        self.assertIn("handle_right", kp1)
        self.assertIn("handle_left_type", kp1)
        self.assertIn("handle_right_type", kp1)

        summary = result["diagnostic_summary"]
        self.assertIn("BEZIER interpolation", summary)
        self.assertIn("smooth curve", summary.lower())
        print("✅ Scenario 3 Passed: Bezier interpolation with handle data correctly retrieved.")

    # -----------------------------------------------------------------------
    # Scenario 4: Object has action but lacks relevant location keyframes
    # -----------------------------------------------------------------------
    def test_04_action_lacks_relevant_keyframes(self):
        """Verify behavior when object has keyframes for rotation, but user asks about location."""
        self.cube.rotation_euler = (0, 0, 0)
        self.cube.keyframe_insert(data_path="rotation_euler", frame=1)
        self.cube.rotation_euler = (0, 1.57, 0)
        self.cube.keyframe_insert(data_path="rotation_euler", frame=25)

        # Query for location keyframes
        result = get_animation_details("Cube", data_path="location")
        self.assertTrue(result["has_animation_data"])
        self.assertIn("error", result)
        self.assertIn("No keyframes found for property 'location'", result["error"])
        self.assertIn("rotation_euler", result["available_animated_properties"])
        self.assertNotIn("location", result["available_animated_properties"])

        # Also query without data_path filter — should return rotation F-curves
        all_anim = get_animation_details("Cube")
        self.assertIn("rotation_euler", all_anim["animated_properties"])
        self.assertEqual(all_anim["fcurves_inspected"], 3)
        print("✅ Scenario 4 Passed: Missing location keyframes correctly detected with available channels listed.")

    # -----------------------------------------------------------------------
    # Scenario 5: Animation data unavailable or cannot be inspected
    # -----------------------------------------------------------------------
    def test_05_animation_data_unavailable(self):
        """Verify handling of objects with no animation data and invalid object queries."""
        # 5A: Object with no animation data
        result_no_anim = get_animation_details("Cube")
        self.assertFalse(result_no_anim["has_animation_data"])
        self.assertIn("no animation data", result_no_anim["diagnostic_summary"].lower())

        # 5B: Non-existent object
        result_missing_obj = get_animation_details("NonExistentObject")
        self.assertIn("error", result_missing_obj)
        self.assertIn("not found", result_missing_obj["error"])
        print("✅ Scenario 5 Passed: Unavailable animation data and missing objects handled gracefully.")

    # -----------------------------------------------------------------------
    # Scenario 6: Stepped F-curve modifier overrides Bezier interpolation
    # -----------------------------------------------------------------------
    def test_06_fcurve_stepped_modifier(self):
        """Verify that F-curve modifiers (e.g. Stepped) are detected and flagged in diagnosis."""
        self.cube.location = (0, 0, 0)
        self.cube.keyframe_insert(data_path="location", frame=1)
        self.cube.location = (4, 0, 0)
        self.cube.keyframe_insert(data_path="location", frame=20)

        # Add a STEPPED modifier to Location X
        fcurves = _get_object_fcurves(self.cube, "location")
        fc_x = next(fc for fc in fcurves if fc.array_index == 0)
        mod = fc_x.modifiers.new(type='STEPPED')
        mod.frame_step = 3.0

        result = get_animation_details("Cube", data_path="location")
        fc_res = next(fc for fc in result["fcurves"] if fc["channel"] == "Location X")
        self.assertIn("fcurve_modifiers", fc_res)
        self.assertEqual(fc_res["fcurve_modifiers"][0]["type"], "STEPPED")
        self.assertEqual(fc_res["fcurve_modifiers"][0]["frame_step"], 3.0)

        summary = result["diagnostic_summary"]
        self.assertIn("Stepped modifier", summary)
        print("✅ Scenario 6 Passed: F-curve modifier detected and highlighted in diagnostic summary.")


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAnimationInspection)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    run_tests()
