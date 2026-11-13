# BlenderMentor Diagnostic Pipeline Audit: Top Three Weaknesses

---

### Weakness 1: Stale Geometry & Bounding Box Inspection in Edit Mode

#### 1. Concrete Example of Failure
A user is in **Edit Mode** fixing a Mirror modifier gap, setting up a Boolean cutter, or adjusting an origin offset. They translate vertices along X towards the mirror plane (X=0) or delete half the mesh. They then ask:
> *"Why is there still a gap in my mirror modifier?"* or *"Did I fix the origin offset?"*

Even though the user has moved the geometry on their screen, BlenderMentor will inspect the scene and falsely conclude that the vertices are still at their old coordinates and that the gap still exists. It will instruct the user to perform moves they have already completed, directly contradicting what the user sees.

#### 2. Root Cause in Implementation
In `addon/scene_reader.py` inside `get_mesh_geometry_summary` (lines 710–730):
```python
mesh = obj.data
verts = mesh.vertices
bb = obj.bound_box
bb_min = [min(bb[i][j] for i in range(8)) for j in range(3)]
...
has_verts_on_x0 = any(abs(v.co.x) < tol for v in verts)
```
* **Un-synchronized Edit Buffer**: In Blender, active edits in Edit Mode exist in a temporary editing buffer. `mesh.vertices` is frozen at the state it was in when Edit Mode was entered until `obj.update_from_editmode()` is called.
* **Stale Object Bounding Box**: Furthermore, `obj.bound_box` is an Object Mode cache. In live testing on Blender 5.1, even after moving vertices by +10.0 units in Edit Mode, `obj.bound_box` remained frozen at its pre-edit value (`-1.0` to `1.0`).
* Because `get_mesh_geometry_summary` reads `obj.bound_box` and un-updated `mesh.vertices`, it returns stale pre-edit geometry.

#### 3. Smallest Effective Fix
In `get_mesh_geometry_summary(object_name)`:
1. If the object is in Edit Mode (`obj.mode == 'EDIT'` or `getattr(obj.data, "is_editmode", False)`), call `obj.update_from_editmode()`.
2. Compute `bb_min` and `bb_max` directly from the vertex coordinates:
   ```python
   bb_min = [min(v.co[j] for v in verts) for j in range(3)]
   bb_max = [max(v.co[j] for v in verts) for j in range(3)]
   ```
   This is O(N), takes <1 ms, and completely bypasses the stale `obj.bound_box` cache, guaranteeing 100% accurate coordinates in both Object Mode and Edit Mode.

#### 4. Regression Test
* **Setup**: Add a cube, enter Edit Mode, translate all vertices by `(+10.0, 0, 0)`.
* **Execution**: Call `get_mesh_geometry_summary("Cube")` while still in Edit Mode.
* **Assertion**: Verify `bounding_box_min[0]` is `9.0`, `bounding_box_max[0]` is `11.0`, and `has_vertices_on_x_plane` is `False`.

---

### Weakness 2: Complete Blindness to Material Slots and Shader Node Networks

#### 1. Concrete Example of Failure
A user asks:
> *"Why is my cube completely purple / pink?"* or *"Why is my material not showing up in the viewport?"* or *"I changed the material color, why didn't the object change?"*

Common real-world causes:
1. An Image Texture node has a missing or broken file path (classic bright pink texture).
2. The `Material Output` node's **Surface** input is disconnected (renders solid black).
3. The material is assigned to slot 1, but all mesh faces are still assigned to slot 0.
4. An empty material slot (`material_slots[0].material is None`).
5. `material.use_nodes` is `False`.

Currently, BlenderMentor will see that a material named `"Material"` exists, assume the material is functioning, and guess unrelated issues (such as viewport shading or scene lighting).

#### 2. Root Cause in Implementation
In `addon/scene_reader.py` (lines 76–78 and 261–262):
```python
if hasattr(obj, "data") and hasattr(obj.data, "materials"):
    result["materials"] = [m.name for m in obj.data.materials if m]
```
That is all the diagnostic pipeline retrieves for materials: a list of string names. 
There is **no tool in `TOOL_REGISTRY` to inspect material slots or shader nodes**. The AI is completely blind to:
* Material slots vs. mesh polygon assignments.
* Whether `use_nodes` is enabled.
* Disconnected shader sockets (e.g., disconnected `Material Output`).
* Missing texture files (`image.filepath` invalid or `image.has_data == False`).

#### 3. Smallest Effective Fix
1. Add an on-demand tool `get_material_details(object_name, slot_index=None)`:
   - Inspects `obj.material_slots` (empty slots, link type: Object vs. Data).
   - Checks `material.use_nodes`.
   - Checks if the active `Material Output` node has its `"Surface"` input connected.
   - Detects missing/broken image files on `ShaderNodeTexImage` nodes.
   - Summarizes the active BSDF type (Principled, Emission, Glass).
2. Register `get_material_details` in `TOOL_REGISTRY` and authorize it in `SYSTEM_PROMPT` Rule 12 and Section A.

#### 4. Regression Test
* **Setup**: Create a Cube with a material where the `Material Output` node's Surface socket is disconnected.
* **Execution**: Call `get_material_details("Cube")`.
* **Assertion**: Verify `output_surface_connected` is `False`, and `diagnostic_summary` flags that the shader is not connected to the Material Output.

---

### Weakness 3: Edit Mode Sub-Element Selection Blindness

#### 1. Concrete Example of Failure
A user in Edit Mode presses a shortcut or clicks a modeling tool and asks:
> *"Why did Bevel / Extrude / Inset do nothing?"* or *"Why is Merge by Distance not working?"*

The #1 beginner mistake in Edit Mode is having **zero vertices/faces selected** (or being in Vertex select mode while trying an operation that requires Edges).

Currently, BlenderMentor will run `get_selection_info` and see:
```json
{ "active_object": "Cube", "selected_objects": [{"name": "Cube"}], "selection_count": 1 }
```
Because the tool reports that the Cube is selected (`selection_count: 1`), BlenderMentor assumes the selection is valid. It will miss the actual problem and hallucinate complex causes like locked transforms, modifier conflicts, or broken tool settings.

#### 2. Root Cause in Implementation
In `addon/scene_reader.py` lines 810–830 (`get_selection_info`):
```python
selected = getattr(bpy.context, "selected_objects", None)
...
result = {
    "active_object": active.name if active else None,
    "selected_objects": [{"name": o.name, "type": o.type} for o in selected],
    "selection_count": len(selected),
}
```
`get_selection_info` only inspects **Object Mode selection**. In Edit Mode, `selected_objects` is always `[active_object]`. It has zero visibility into:
* Which mesh selection mode is active (`Vertex`, `Edge`, or `Face`).
* How many vertices, edges, or faces are actually selected in Edit Mode.

#### 3. Smallest Effective Fix
In `get_selection_info()`:
If the active object is a mesh and the mode is `EDIT_MESH` (or `obj.mode == 'EDIT'`):
1. Read the active selection mode from `bpy.context.tool_settings.mesh_select_mode` (`[vertex, edge, face]`).
2. Read the instant, built-in C-level properties on `obj.data` (after `obj.update_from_editmode()`):
   ```python
   "sub_element_selection": {
       "mode": {"vertex": sel_mode[0], "edge": sel_mode[1], "face": sel_mode[2]},
       "selected_vertices": obj.data.total_vert_sel,
       "selected_edges": obj.data.total_edge_sel,
       "selected_faces": obj.data.total_face_sel,
   }
   ```
   *(These properties are instantaneous O(1) counters maintained natively by Blender without iterating geometry).*
3. If all counts are 0, include `"has_sub_element_selection": False` in the diagnostic summary.

#### 4. Regression Test
* **Setup**: Add a cube, enter Edit Mode, and deselect all (`bpy.ops.mesh.select_all(action='DESELECT')`).
* **Execution**: Call `get_selection_info()`.
* **Assertion**: Verify `selected_vertices == 0`, `has_sub_element_selection == False`, and `mesh_select_mode == [True, False, False]`.

---

### Summary of Impact

| Weakness | Affected Workflows | Failure Type | Fix Complexity |
|---|---|---|---|
| **1. Stale Edit Mode Geometry** | Modeling, Mirror seams, origin offsets, vertex counts, symmetry | Reads outdated pre-edit coordinates; contradicts user | **Small** (add `update_from_editmode()` + vertex min/max) |
| **2. Material/Shader Blindness** | Shading, Texturing, Rendering, Viewport, Pink/Black materials | Fails to diagnose disconnected nodes, broken textures, empty slots | **Medium** (add `get_material_details` tool) |
| **3. Sub-Element Selection Blindness** | Extrude, Bevel, Inset, Loop Cut, UV unwrap, Mesh cleanup | Falsely assumes selection exists; diagnoses fictional issues | **Small** (use native `mesh.total_vert_sel` in `get_selection_info`) |
