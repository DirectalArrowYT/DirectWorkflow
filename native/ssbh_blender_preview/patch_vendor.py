"""Apply the viewport's small renderer extensions to the pinned vendor copy."""
from pathlib import Path


def patch(root, file, old, new):
    path = root / file
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"Unexpected vendor source: {path}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main():
    root = Path(__file__).resolve().parent.parent / "vendor/ssbh_wgpu/ssbh_wgpu/src"
    patch(root, "shader/skinning.wgsl", "parent_index: vec4<i32>\n", "parent_index: vec4<i32>,\n    object_transform: mat4x4<f32>,\n    normal_transform: mat4x4<f32>,\n")
    patch(root, "shader/skinning.wgsl", "    var out: VertexInput0;", """    position = (mesh_object_info.object_transform * vec4(position, 1.0)).xyz;
    normal = (mesh_object_info.normal_transform * vec4(normal, 0.0)).xyz;
    tangent = (mesh_object_info.object_transform * vec4(tangent, 0.0)).xyz;
    var out: VertexInput0;""")
    patch(root, "model.rs", "    mesh_object_info_bind_group: crate::shader::skinning::bind_groups::BindGroup2,", """    mesh_object_info_bind_group: crate::shader::skinning::bind_groups::BindGroup2,
    mesh_object_info_buffer: wgpu::Buffer,
    parent_index: i32,""")
    patch(root, "model.rs", "struct BoneRenderData {", """impl RenderMesh {
    /// Apply Blender object movement after skeletal deformation.
    pub fn set_object_transform(&self, queue: &wgpu::Queue, transform: glam::Mat4) {
        queue.write_buffer(&self.mesh_object_info_buffer, 0, bytemuck::bytes_of(
            &crate::shader::skinning::MeshObjectInfo {
                parent_index: glam::IVec4::new(self.parent_index, -1, -1, -1),
                object_transform: transform,
                normal_transform: transform.inverse().transpose(),
            }
        ));
    }
}

struct BoneRenderData {""")
    patch(root, "model/mesh_creation.rs", "                parent_index: glam::IVec4::new(parent_index, -1, -1, -1),", """                parent_index: glam::IVec4::new(parent_index, -1, -1, -1),
                object_transform: glam::Mat4::IDENTITY,
                normal_transform: glam::Mat4::IDENTITY,""")
    patch(root, "model/mesh_creation.rs", "            wgpu::BufferUsages::UNIFORM,\n        );\n\n        let mesh_object_info_bind_group", "            wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,\n        );\n\n        let mesh_object_info_bind_group")
    patch(root, "model/mesh_creation.rs", "            mesh_object_info_bind_group,", "            mesh_object_info_bind_group,\n            mesh_object_info_buffer,\n            parent_index,")
    patch(root, "uniforms.rs", "    let lighting_settings = program", """    // Opaque draw calls cover the framebuffer even when animated material alpha is zero.
    let mut shader_settings = shader_settings;
    shader_settings.w = material.shader_label.ends_with("_opaque") as u32;

    let lighting_settings = program""")
    patch(root, "shader/model.wgsl", "    return vec4(outColor, outAlpha);", """    // Preserve coverage for the viewport compositor and transparent captures.
    if per_material.shader_settings.w == 1u {
        outAlpha = 1.0;
    }
    return vec4(outColor, outAlpha);""")


if __name__ == "__main__":
    main()
