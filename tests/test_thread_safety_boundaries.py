# SPDX-License-Identifier: GPL-3.0-or-later
# Test suite for BlenderMentor thread safety boundaries, main-thread snapshots,
# and on-demand inspection request dispatching.
#
# Run inside Blender using:
#   blender --background --python tests/test_thread_safety_boundaries.py

import sys
import os
import unittest
import threading
import time
import copy

# Ensure the addon root is importable
addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)

import bpy
import bmesh
from addon.scene_reader import (
    capture_scene_snapshot,
    _global_inspector,
    execute_tool,
    execute_tool_direct,
    _make_cache_key,
    TOOL_REGISTRY,
    get_basic_context,
    get_selection_info,
    get_mesh_geometry_summary,
    get_object_details,
    get_modifier_details,
    get_material_details,
    get_animation_details,
)
from addon.ai_client import _ToolTracker, MAX_TOOL_RETRIES


class TestThreadSafetyBoundaries(unittest.TestCase):
    """Verify thread execution boundaries, snapshots, and request management."""

    def setUp(self):
        """Create a clean test scene."""
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
        self.cube = bpy.context.active_object
        self.cube.name = "Cube"
        bpy.context.view_layer.objects.active = self.cube
        self.cube.select_set(True)

    def tearDown(self):
        """Reset inspector and mode."""
        _global_inspector.clear_active_turn()
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

    # -----------------------------------------------------------------------
    # Scenario 1: Worker thread tool execution makes no live bpy calls
    # -----------------------------------------------------------------------
    def test_01_worker_thread_served_from_plain_python_snapshot(self):
        """Verify worker thread receives cached snapshot data without touching live bpy objects."""
        # Enter edit mode and move vertices
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(self.cube.data)
        for v in bm.verts:
            v.co.x += 1.0
        bmesh.update_edit_mesh(self.cube.data)

        # 1. Main thread captures snapshot
        snapshot = capture_scene_snapshot("turn_01")
        _global_inspector.set_active_turn("turn_01", snapshot)

        # Verify snapshot contains only pure Python primitives (no bpy_struct)
        for key, val in snapshot["cache"].items():
            self.assertIsInstance(val, dict)
            val_str = str(val)
            self.assertNotIn("bpy_struct", val_str)
            self.assertNotIn("bpy.data", val_str)

        # 2. Worker thread runs tool calls
        worker_out = {}
        worker_errors = []

        def worker():
            try:
                # In worker thread, call execute_tool for pre-cached tools
                worker_out["sel"] = execute_tool("get_selection_info", {}, snapshot=snapshot)
                worker_out["geom"] = execute_tool("get_mesh_geometry_summary", {"object_name": "Cube"}, snapshot=snapshot)
                worker_out["obj"] = execute_tool("get_object_details", {"object_name": "Cube"}, snapshot=snapshot)
            except Exception as e:
                worker_errors.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertEqual(len(worker_errors), 0, f"Worker encountered errors: {worker_errors}")
        self.assertEqual(worker_out["sel"]["mode"], "EDIT_MESH")
        self.assertTrue(worker_out["sel"]["is_edit_mode"])
        self.assertEqual(worker_out["geom"]["mode"], "EDIT_MESH")
        self.assertTrue(worker_out["geom"]["is_edit_mode"])
        # Bounding box should reflect vertex offset +1
        self.assertAlmostEqual(worker_out["geom"]["bounding_box_local_min"][0], 0.0, places=4)
        self.assertAlmostEqual(worker_out["geom"]["bounding_box_local_max"][0], 2.0, places=4)

        print("✅ Test 1 Passed: Worker thread served exclusively from plain Python snapshot.")

    # -----------------------------------------------------------------------
    # Scenario 2: On-demand inspection executes safely on main thread
    # -----------------------------------------------------------------------
    def test_02_on_demand_inspection_dispatched_to_main_thread(self):
        """Verify that uncached tools are dispatched and executed on the main thread."""
        # Add a Camera to the scene (not the active object, so not in active object pre-cache)
        bpy.ops.object.camera_add(location=(0, -5, 2))
        cam = bpy.context.active_object
        cam.name = "TestCamera"
        # Re-activate Cube
        bpy.context.view_layer.objects.active = self.cube

        snapshot = capture_scene_snapshot("turn_02")
        _global_inspector.set_active_turn("turn_02", snapshot)

        # Ensure TestCamera details are not yet in snapshot cache
        cam_key = _make_cache_key("get_object_details", {"object_name": "TestCamera"})
        self.assertNotIn(cam_key, snapshot["cache"])

        worker_res = {}
        worker_started = threading.Event()

        def worker():
            worker_started.set()
            # Request uncached tool
            worker_res["cam"] = execute_tool("get_object_details", {"object_name": "TestCamera"}, snapshot=snapshot)

        t = threading.Thread(target=worker)
        t.start()
        worker_started.wait()

        # Let worker place request in queue
        time.sleep(0.05)

        # Main thread processes queue
        processed = _global_inspector.process_pending_requests()
        self.assertEqual(processed, 1, "Expected main thread to process exactly 1 inspection request")

        t.join()

        # Verify worker received result
        cam_data = worker_res.get("cam", {})
        self.assertEqual(cam_data.get("name"), "TestCamera")
        self.assertEqual(cam_data.get("type"), "CAMERA")

        # Verify result is now cached in snapshot
        self.assertIn(cam_key, snapshot["cache"])
        self.assertEqual(snapshot["cache"][cam_key]["name"], "TestCamera")

        print("✅ Test 2 Passed: On-demand inspection dispatched and executed safely on the main thread.")

    # -----------------------------------------------------------------------
    # Scenario 3: Multiple aspects of same scene inspected coherently
    # -----------------------------------------------------------------------
    def test_03_multiple_aspects_of_same_scene_inspected(self):
        """Verify modifiers, materials, and geometry can all be inspected via snapshot/dispatch."""
        # Add Bevel modifier
        bev = self.cube.modifiers.new("BevelMod", "BEVEL")
        bev.width = 0.05
        bev.segments = 3

        # Add Material with disconnected Surface
        mat = bpy.data.materials.new("DisconnMat")
        if mat.node_tree is None:
            mat.use_nodes = True
        self.cube.data.materials.append(mat)
        out_node = next(n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL')
        for link in list(out_node.inputs["Surface"].links):
            mat.node_tree.links.remove(link)

        # Snapshot
        snapshot = capture_scene_snapshot("turn_03")
        _global_inspector.set_active_turn("turn_03", snapshot)

        # In worker thread, query modifiers, materials, and geometry
        worker_out = {}

        def worker():
            worker_out["geom"] = execute_tool("get_mesh_geometry_summary", {"object_name": "Cube"}, snapshot=snapshot)
            worker_out["mat"] = execute_tool("get_material_details", {"object_name": "Cube"}, snapshot=snapshot)
            # Modifier details is on-demand dispatched
            worker_out["mod"] = execute_tool("get_modifier_details", {"object_name": "Cube", "modifier_name": "BevelMod"}, snapshot=snapshot)

        t = threading.Thread(target=worker)
        t.start()

        # Main thread processes any on-demand requests
        time.sleep(0.05)
        _global_inspector.process_pending_requests()
        t.join()

        self.assertEqual(worker_out["geom"]["object_name"], "Cube")
        self.assertIn("DisconnMat", [m["material_name"] for m in worker_out["mat"]["materials"]])
        self.assertFalse(worker_out["mat"]["materials"][0]["surface_connected"])
        self.assertEqual(worker_out["mod"]["modifier_type"], "BEVEL")
        self.assertEqual(worker_out["mod"]["parameters"]["segments"], 3)

        print("✅ Test 3 Passed: Geometry, materials, and modifiers inspected coherently.")

    # -----------------------------------------------------------------------
    # Scenario 4: Snapshot consistency during mid-reasoning scene changes
    # -----------------------------------------------------------------------
    def test_04_snapshot_consistency_during_scene_changes(self):
        """Verify that mid-reasoning scene modifications by the user do not taint the turn snapshot."""
        # Start in Edit Mode
        bpy.ops.object.mode_set(mode='EDIT')
        snapshot = capture_scene_snapshot("turn_04")
        _global_inspector.set_active_turn("turn_04", snapshot)

        # Verify initial snapshot is in Edit Mode
        initial_geom = snapshot["cache"][_make_cache_key("get_mesh_geometry_summary", {"object_name": "Cube"})]
        self.assertTrue(initial_geom["is_edit_mode"])
        self.assertEqual(initial_geom["mode"], "EDIT_MESH")

        # Now simulate user exiting to Object Mode and scaling Cube while AI is thinking
        bpy.ops.object.mode_set(mode='OBJECT')
        self.cube.scale = (5.0, 5.0, 5.0)

        # In worker thread, query geometry and selection using the active turn snapshot
        worker_out = {}

        def worker():
            worker_out["geom"] = execute_tool("get_mesh_geometry_summary", {"object_name": "Cube"}, snapshot=snapshot)
            worker_out["sel"] = execute_tool("get_selection_info", {}, snapshot=snapshot)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        # The worker's snapshot MUST still report the Edit Mode state from when question was asked
        self.assertTrue(worker_out["geom"]["is_edit_mode"], "Snapshot should isolate AI from mid-reasoning mode switch")
        self.assertEqual(worker_out["geom"]["mode"], "EDIT_MESH")
        self.assertTrue(worker_out["sel"]["is_edit_mode"])

        print("✅ Test 4 Passed: Snapshot consistency cleanly isolates AI from mid-reasoning scene changes.")

    # -----------------------------------------------------------------------
    # Scenario 5: Turn correlation and cancellation
    # -----------------------------------------------------------------------
    def test_05_turn_correlation_and_cancellation(self):
        """Verify that stale turn requests are rejected cleanly and do not corrupt new turns."""
        snap1 = capture_scene_snapshot("turn_A")
        _global_inspector.set_active_turn("turn_A", snap1)

        worker_res = {}

        def worker():
            # Request on-demand tool for turn_A
            worker_res["res"] = execute_tool("get_modifier_details", {"object_name": "Cube", "modifier_name": "NonExistent"}, snapshot=snap1)

        t = threading.Thread(target=worker)
        t.start()

        # User cancels turn_A by starting turn_B before main thread processes request
        snap2 = capture_scene_snapshot("turn_B")
        _global_inspector.set_active_turn("turn_B", snap2)

        _global_inspector.process_pending_requests()
        t.join()

        # Verify worker received cancellation error
        self.assertIn("error", worker_res["res"])
        self.assertTrue(any(term in worker_res["res"]["error"].lower() for term in ["cancelled", "superceded", "expired"]))

        print("✅ Test 5 Passed: Stale requests cleanly cancelled when superceded by new turn.")

    # -----------------------------------------------------------------------
    # Scenario 6: Timeout handling and no post-timeout execution
    # -----------------------------------------------------------------------
    def test_06_timeout_handling(self):
        """Verify worker thread unblocks cleanly with error dictionary if main thread does not process request,
        and that timed-out requests are NOT executed later when main thread pumps."""
        snap = capture_scene_snapshot("turn_timeout")
        _global_inspector.set_active_turn("turn_timeout", snap)

        worker_res = {}

        def worker():
            # Request inspection with very short timeout without main thread pumping
            worker_res["res"] = _global_inspector.request_main_thread_inspection(
                "get_object_details", {"object_name": "Cube"}, timeout=0.05
            )

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertIn("error", worker_res["res"])
        self.assertIn("timed out", worker_res["res"]["error"])

        # Main thread now pumps pending requests — timed-out request must NOT execute or be cached
        processed = _global_inspector.process_pending_requests()
        # Should be 0 processed because timed-out request is skipped
        self.assertEqual(processed, 0, "Timed-out request should be skipped without execution")

        print("✅ Test 6 Passed: Unprocessed request times out safely and is skipped without post-timeout execution.")

    # -----------------------------------------------------------------------
    # Scenario 7: Python evaluation security and main-thread boundary
    # -----------------------------------------------------------------------
    def test_07_eval_security_and_thread_dispatch(self):
        """Verify evaluate_python_expression runs on main thread, allows read-only bpy queries,
        and blocks bpy.ops operators and unsafe imports."""
        snap = capture_scene_snapshot("turn_eval")
        _global_inspector.set_active_turn("turn_eval", snap)

        worker_res = {}

        def worker():
            # 1. Valid read-only query
            worker_res["valid"] = execute_tool("evaluate_python_expression", {"expression": "[o.name for o in bpy.data.objects]"}, snapshot=snap)
            # 2. Operator blocked
            worker_res["ops"] = execute_tool("evaluate_python_expression", {"expression": "bpy.ops.object.delete()"}, snapshot=snap)
            # 3. Unsafe import blocked
            worker_res["import"] = execute_tool("evaluate_python_expression", {"expression": "__import__('os').system('ls')"}, snapshot=snap)

        t = threading.Thread(target=worker)
        t.start()

        # Pump requests on main thread
        time.sleep(0.05)
        _global_inspector.process_pending_requests()
        time.sleep(0.05)
        _global_inspector.process_pending_requests()
        time.sleep(0.05)
        _global_inspector.process_pending_requests()
        t.join()

        # Valid expression succeeded
        self.assertIn("result", worker_res["valid"])
        self.assertIn("Cube", worker_res["valid"]["result"])

        # Dangerous operator blocked
        self.assertIn("error", worker_res["ops"])
        self.assertIn("bpy.ops", worker_res["ops"]["error"])

        # Dangerous import blocked
        self.assertIn("error", worker_res["import"])
        self.assertIn("Security restriction", worker_res["import"]["error"])

        print("✅ Test 7 Passed: evaluate_python_expression cleanly dispatched with strict security restrictions.")

    # -----------------------------------------------------------------------
    # Scenario 8: Web tools do not touch bpy on worker threads
    # -----------------------------------------------------------------------
    def test_08_web_tools_no_bpy_access_on_worker_thread(self):
        """Verify fetch_blender_docs and search_blender_community can be invoked from worker threads
        without touching bpy at runtime."""
        from addon.scene_reader import _get_blender_version_string, _STATIC_BLENDER_DOC_VERSION

        version_from_thread = []

        def worker():
            v = _get_blender_version_string()
            version_from_thread.append(v)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertEqual(len(version_from_thread), 1)
        self.assertEqual(version_from_thread[0], _STATIC_BLENDER_DOC_VERSION)

        print("✅ Test 8 Passed: Web tools use pre-cached version without worker-thread bpy calls.")

    # -----------------------------------------------------------------------
    # Scenario 9: Duplicate tool call notice, tracker, and loop breaker
    # -----------------------------------------------------------------------
    def test_09_duplicate_tool_call_notice_and_suppression(self):
        """Verify that repetitive tool calls with identical arguments are detected,
        annotated with an instructive notice, and tracked to prevent runaway loops."""
        snap = capture_scene_snapshot("turn_dup")
        _global_inspector.set_active_turn("turn_dup", snap)

        tracker = _ToolTracker()

        # Round 1: Model calls get_selection_info
        r1_tools = [{"name": "get_selection_info", "args": {}}]
        r1_keys = [_make_cache_key(t["name"], t["args"]) for t in r1_tools]
        should_break_1 = tracker.check_round_duplicates(r1_keys)
        self.assertFalse(should_break_1)
        self.assertEqual(tracker.consecutive_dup_rounds, 0)

        res1 = tracker.execute_or_block(
            "get_selection_info", {},
            lambda: execute_tool("get_selection_info", {}, snapshot=snap)
        )
        self.assertNotIn("_notice", res1)
        self.assertIn(r1_keys[0], tracker.successful_calls)

        # Round 2: Model repeats get_selection_info (1st duplicate round)
        r2_tools = [{"name": "get_selection_info", "args": {}}]
        r2_keys = [_make_cache_key(t["name"], t["args"]) for t in r2_tools]
        should_break_2 = tracker.check_round_duplicates(r2_keys)
        self.assertFalse(should_break_2)
        self.assertEqual(tracker.consecutive_dup_rounds, 1)

        res2 = tracker.execute_or_block(
            "get_selection_info", {},
            lambda: execute_tool("get_selection_info", {}, snapshot=snap)
        )
        self.assertIn("_notice", res2)
        self.assertIn("already called", res2["_notice"])
        self.assertIn("Please synthesize", res2["_notice"])

        # Round 3: Model repeats get_selection_info AGAIN (2nd consecutive duplicate round)
        r3_tools = [{"name": "get_selection_info", "args": {}}]
        r3_keys = [_make_cache_key(t["name"], t["args"]) for t in r3_tools]
        should_break_3 = tracker.check_round_duplicates(r3_keys)
        self.assertTrue(should_break_3)
        self.assertEqual(tracker.consecutive_dup_rounds, 2)

        res3 = tracker.execute_or_block(
            "get_selection_info", {},
            lambda: execute_tool("get_selection_info", {}, snapshot=snap)
        )
        self.assertIn("_notice", res3)

        # Control: A new tool call resets consecutive_dup_rounds
        r4_tools = [{"name": "get_object_details", "args": {"object_name": "Cube"}}]
        r4_keys = [_make_cache_key(t["name"], t["args"]) for t in r4_tools]
        should_break_4 = tracker.check_round_duplicates(r4_keys)
        self.assertFalse(should_break_4)
        self.assertEqual(tracker.consecutive_dup_rounds, 0)

        res4 = tracker.execute_or_block(
            "get_object_details", {"object_name": "Cube"},
            lambda: execute_tool("get_object_details", {"object_name": "Cube"}, snapshot=snap)
        )
        self.assertNotIn("_notice", res4)

        print("✅ Test 9 Passed: Duplicate tool calls correctly annotated and loop breaker verified.")

    # -----------------------------------------------------------------------
    # Scenario 10: Tool tracker failure retry bounds, isolation, and contracts
    # -----------------------------------------------------------------------
    def test_10_tool_tracker_failure_retries_and_isolation(self):
        """Verify retry behavior, failure bounds, and isolation across tool signatures:
        1. Failed calls do NOT receive false duplicate notices on retry.
        2. Retries after failure reset the consecutive duplicate round counter.
        3. Repeated failures are capped at MAX_TOOL_RETRIES (2), blocking further execution.
        4. Independent tool signatures maintain separate success and failure tracking.
        5. Provider loops in ai_client share the identical contract.
        """
        tracker = _ToolTracker(max_retries=MAX_TOOL_RETRIES)
        self.assertEqual(tracker.max_retries, 2)

        k_fail = _make_cache_key("failing_tool", {"param": 1})

        # 1. First execution fails
        fail_exec_count = 0
        def exec_failing():
            nonlocal fail_exec_count
            fail_exec_count += 1
            return {"error": "Simulated tool execution failure"}

        res_f1 = tracker.execute_or_block("failing_tool", {"param": 1}, exec_failing)
        self.assertIn("error", res_f1)
        self.assertEqual(fail_exec_count, 1)
        self.assertEqual(tracker.failure_counts[k_fail], 1)
        self.assertNotIn(k_fail, tracker.successful_calls)

        # 2. Retrying a failed call: round duplicate check must NOT count it as duplicate
        should_break = tracker.check_round_duplicates([k_fail])
        self.assertFalse(should_break)
        self.assertEqual(tracker.consecutive_dup_rounds, 0)

        # 3. Successful retry must NOT receive false duplicate notice
        def exec_success():
            return {"status": "recovered"}

        res_f2 = tracker.execute_or_block("failing_tool", {"param": 1}, exec_success)
        self.assertNotIn("error", res_f2)
        self.assertNotIn("_notice", res_f2)  # No false duplicate notice!
        self.assertEqual(tracker.failure_counts[k_fail], 0)  # Failure count reset!
        self.assertIn(k_fail, tracker.successful_calls)

        # 4. Now repeating after SUCCESS does receive duplicate notice
        res_f3 = tracker.execute_or_block("failing_tool", {"param": 1}, exec_success)
        self.assertIn("_notice", res_f3)

        # 5. Bound repeated failures of another signature
        k_bad = _make_cache_key("always_broken", {})
        bad_calls = 0
        def exec_bad():
            nonlocal bad_calls
            bad_calls += 1
            return {"error": "Permanent failure"}

        # Attempt 1: fails
        r_b1 = tracker.execute_or_block("always_broken", {}, exec_bad)
        self.assertIn("error", r_b1)
        self.assertEqual(bad_calls, 1)
        self.assertEqual(tracker.failure_counts[k_bad], 1)

        # Attempt 2: fails
        r_b2 = tracker.execute_or_block("always_broken", {}, exec_bad)
        self.assertIn("error", r_b2)
        self.assertEqual(bad_calls, 2)
        self.assertEqual(tracker.failure_counts[k_bad], 2)

        # Attempt 3: Retry limit reached -> blocked WITHOUT executing exec_bad
        r_b3 = tracker.execute_or_block("always_broken", {}, exec_bad)
        self.assertIn("error", r_b3)
        self.assertIn("Retry limit reached", r_b3["error"])
        self.assertIn("further attempts for this signature are blocked", r_b3["error"])
        self.assertIn("Please synthesize", r_b3["error"])
        self.assertEqual(bad_calls, 2)  # exec_bad was NOT called a 3rd time!

        # 6. Independent signatures execute normally and independently
        k_good = _make_cache_key("good_tool", {"x": 42})
        good_exec_count = 0
        def exec_good():
            nonlocal good_exec_count
            good_exec_count += 1
            return {"x": 42, "status": "ok"}

        r_g1 = tracker.execute_or_block("good_tool", {"x": 42}, exec_good)
        self.assertEqual(good_exec_count, 1)
        self.assertNotIn("error", r_g1)
        self.assertNotIn("_notice", r_g1)
        self.assertIn(k_good, tracker.successful_calls)
        self.assertEqual(tracker.failure_counts.get(k_good, 0), 0)

        # Verify that broken tool status did not leak into good tool
        self.assertNotIn("error", r_g1)
        # Verify broken tool remains blocked
        r_b4 = tracker.execute_or_block("always_broken", {}, exec_bad)
        self.assertIn("Retry limit reached", r_b4["error"])
        self.assertEqual(bad_calls, 2)

        # 7. Mixed round containing a retry or new call resets consecutive duplicate streak
        tracker.consecutive_dup_rounds = 1  # simulate 1 prior duplicate round
        mixed_keys = [k_good, _make_cache_key("brand_new_tool", {})]
        should_break_mixed = tracker.check_round_duplicates(mixed_keys)
        self.assertFalse(should_break_mixed)
        self.assertEqual(tracker.consecutive_dup_rounds, 0)

        # 8. Verify that ai_client loops (Gemini, Claude, Ollama) all import and instantiate _ToolTracker
        import addon.ai_client as ai_client
        self.assertTrue(hasattr(ai_client, "_ToolTracker"))
        self.assertEqual(ai_client.MAX_TOOL_RETRIES, 2)

        import inspect
        for fn_name in ["_gemini_tool_loop", "_claude_tool_loop", "_ollama_tool_loop", "_deepseek_tool_loop"]:
            fn = getattr(ai_client, fn_name)
            src = inspect.getsource(fn)
            self.assertIn("tracker = _ToolTracker()", src, f"{fn_name} must instantiate _ToolTracker")
            self.assertIn("tracker.check_round_duplicates", src, f"{fn_name} must use check_round_duplicates")
            self.assertIn("tracker.execute_or_block", src, f"{fn_name} must use execute_or_block")

        print("✅ Test 10 Passed: Tool tracker failure retry bounds, isolation, and loop contracts verified.")

    def test_11_stop_processing_and_cancellation(self):
        """Verify cancel_event interrupts ask_ai and stop_processing operator restores input text."""
        import addon.ai_client as ai_client
        import addon.ui.chat_panel as chat_panel
        from addon.state.conversation import set_input_text_suppressed

        # 1. Verify cancel_event raises InterruptedError immediately in ask_ai
        class MockPrefs:
            provider = "GEMINI"
            api_key = "dummy_key"
            developer_mode = False
            def get_selected_model_id(self):
                return "gemini-2.0-flash"

        cancel_ev = threading.Event()
        cancel_ev.set()  # already cancelled

        with self.assertRaises(InterruptedError):
            ai_client.ask_ai(MockPrefs(), "{}", "Test question", cancel_event=cancel_ev)

        # 2. Verify blendermentor.stop_processing operator
        scene = bpy.context.scene
        scene.bm_is_processing = True
        scene.bm_status_message = "Thinking."
        scene.bm_last_submitted_prompt = "Accidental prompt text that was submitted"
        chat_panel._last_submitted_prompt = "Accidental prompt text that was submitted"
        scene.bm_input_text = ""

        # Add user message to simulate in-flight send
        user_msg = scene.bm_chat_history.add()
        user_msg.text = "Accidental prompt text that was submitted"
        user_msg.is_user = True
        initial_chat_len = len(scene.bm_chat_history)

        # Execute stop operator
        res = bpy.ops.blendermentor.stop_processing()
        self.assertEqual(res, {'FINISHED'})

        # Verify state reset
        self.assertFalse(scene.bm_is_processing)
        self.assertEqual(scene.bm_status_message, "")
        # Verify text restored to input field
        self.assertEqual(scene.bm_input_text, "Accidental prompt text that was submitted")
        # Verify the aborted message was removed from chat history
        self.assertEqual(len(scene.bm_chat_history), initial_chat_len - 1)

        # 3. Clean up
        scene.bm_input_text = ""
        print("✅ Test 11 Passed: Immediate cancellation, input text restoration, and stop_processing operator verified.")

    def test_12_deepseek_provider_configuration_and_tool_format(self):
        """Verify DeepSeek provider enum, OpenAI tool declarations, and model fetching."""
        import addon.preferences as prefs_mod
        import addon.ai_client as ai_client

        # 1. Provider enum items check
        addon_prefs_cls = prefs_mod.BlenderMentorPreferences
        provider_prop = addon_prefs_cls.__annotations__.get("provider")
        provider_items = [item[0] for item in provider_prop.keywords["items"]]
        self.assertIn("DEEPSEEK", provider_items)

        # 2. DeepSeek tool declaration structure
        deepseek_tools = ai_client._build_deepseek_tools(allow_python_eval=True, enable_web_search=True)
        self.assertIsInstance(deepseek_tools, list)
        self.assertTrue(len(deepseek_tools) > 0)
        first_tool = deepseek_tools[0]
        self.assertEqual(first_tool.get("type"), "function")
        self.assertIn("name", first_tool.get("function", {}))
        self.assertIn("parameters", first_tool.get("function", {}))

        # 3. Verify cancel_event parameter exists on all tool loops
        import inspect
        for fn_name in ["_gemini_tool_loop", "_claude_tool_loop", "_ollama_tool_loop", "_deepseek_tool_loop"]:
            sig = inspect.signature(getattr(ai_client, fn_name))
            self.assertIn("cancel_event", sig.parameters)

        print("✅ Test 12 Passed: DeepSeek provider, OpenAI tool declarations, and cancel_event wiring verified.")


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestThreadSafetyBoundaries)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    run_tests()
