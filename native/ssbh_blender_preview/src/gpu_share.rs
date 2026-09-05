//! Zero-copy DX12 → OpenGL present for Blender's viewport.
//!
//! NVIDIA WGL_NV_DX_interop2 only registers native D3D11 UNORM textures, not
//! sRGB and not D3D12 resources opened in D3D11. wgpu writes a D3D11 NT-shared
//! UNORM texture; a second legacy-shared D3D11 texture is what GL samples.

#![cfg(windows)]

use std::ffi::{c_void, CString};
use std::ptr;

use wgpu::hal::api::Dx12;
use windows::core::{Interface, PCSTR};
use windows::Win32::Foundation::{
    CloseHandle, DuplicateHandle, GetLastError, SetLastError, DUPLICATE_SAME_ACCESS, HANDLE,
    WAIT_OBJECT_0,
};
use windows::Win32::Graphics::Direct3D::{D3D_DRIVER_TYPE_UNKNOWN, D3D_FEATURE_LEVEL_11_0};
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Resource, ID3D11Texture2D,
    D3D11_BIND_RENDER_TARGET, D3D11_BIND_SHADER_RESOURCE, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
    D3D11_RESOURCE_MISC_SHARED, D3D11_RESOURCE_MISC_SHARED_NTHANDLE, D3D11_SDK_VERSION,
    D3D11_TEXTURE2D_DESC, D3D11_USAGE_DEFAULT,
};
use windows::Win32::Graphics::Direct3D12::{ID3D12CommandQueue, ID3D12Fence, ID3D12Resource};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_FORMAT, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R8G8B8A8_UNORM, DXGI_SAMPLE_DESC,
};
use windows::Win32::Graphics::Dxgi::{
    IDXGIAdapter, IDXGIResource1, DXGI_SHARED_RESOURCE_READ, DXGI_SHARED_RESOURCE_WRITE,
};
use windows::Win32::Graphics::OpenGL::wglGetProcAddress;
use windows::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryA};
use windows::Win32::System::Threading::{CreateEventA, GetCurrentProcess, WaitForSingleObject};

const GL_TEXTURE_2D: u32 = 0x0DE1;
const GL_TEXTURE_RECTANGLE: u32 = 0x84F5;
const GL_FRAGMENT_SHADER: u32 = 0x8B30;
const GL_VERTEX_SHADER: u32 = 0x8B31;
const GL_COMPILE_STATUS: u32 = 0x8B81;
const GL_LINK_STATUS: u32 = 0x8B82;
const GL_ARRAY_BUFFER: u32 = 0x8892;
const GL_STATIC_DRAW: u32 = 0x88E4;
const GL_FLOAT: u32 = 0x1406;
const GL_TRIANGLE_STRIP: u32 = 0x0005;
const GL_BLEND: u32 = 0x0BE2;
const GL_DEPTH_TEST: u32 = 0x0B71;
const GL_FALSE: u8 = 0;
const GL_RGBA8: u32 = 0x8058;
const GL_SRGB8_ALPHA8: u32 = 0x8C43;
const GL_HANDLE_TYPE_D3D12_RESOURCE_EXT: u32 = 0x958A;
const GL_HANDLE_TYPE_D3D11_IMAGE_EXT: u32 = 0x958B;
const WGL_ACCESS_READ_ONLY_NV: u32 = 0x0000;
const WGL_ACCESS_READ_WRITE_NV: u32 = 0x0001;

type WglDxOpenDeviceNv = unsafe extern "system" fn(*mut c_void) -> HANDLE;
type WglDxCloseDeviceNv = unsafe extern "system" fn(HANDLE) -> i32;
type WglDxRegisterObjectNv =
    unsafe extern "system" fn(HANDLE, *mut c_void, u32, u32, u32) -> HANDLE;
type WglDxUnregisterObjectNv = unsafe extern "system" fn(HANDLE, HANDLE) -> i32;
type WglDxLockObjectsNv = unsafe extern "system" fn(HANDLE, i32, *mut HANDLE) -> i32;
type WglDxUnlockObjectsNv = unsafe extern "system" fn(HANDLE, i32, *mut HANDLE) -> i32;
type GlCreateMemoryObjectsExt = unsafe extern "system" fn(i32, *mut u32);
type GlImportMemoryWin32HandleExt = unsafe extern "system" fn(u32, u64, u32, *mut c_void);
type GlTexStorageMem2DExt =
    unsafe extern "system" fn(u32, i32, u32, i32, i32, u32, u64);
type GlDeleteMemoryObjectsExt = unsafe extern "system" fn(i32, *const u32);

#[derive(Clone, Copy)]
struct GlFns {
    create_shader: unsafe extern "system" fn(u32) -> u32,
    shader_source: unsafe extern "system" fn(u32, i32, *const *const i8, *const i32),
    compile_shader: unsafe extern "system" fn(u32),
    get_shader_iv: unsafe extern "system" fn(u32, u32, *mut i32),
    create_program: unsafe extern "system" fn() -> u32,
    attach_shader: unsafe extern "system" fn(u32, u32),
    link_program: unsafe extern "system" fn(u32),
    get_program_iv: unsafe extern "system" fn(u32, u32, *mut i32),
    use_program: unsafe extern "system" fn(u32),
    gen_textures: unsafe extern "system" fn(i32, *mut u32),
    bind_texture: unsafe extern "system" fn(u32, u32),
    tex_parameteri: unsafe extern "system" fn(u32, u32, i32),
    gen_buffers: unsafe extern "system" fn(i32, *mut u32),
    bind_buffer: unsafe extern "system" fn(u32, u32),
    buffer_data: unsafe extern "system" fn(u32, isize, *const c_void, u32),
    gen_vertex_arrays: unsafe extern "system" fn(i32, *mut u32),
    bind_vertex_array: unsafe extern "system" fn(u32),
    enable_vertex_attrib_array: unsafe extern "system" fn(u32),
    vertex_attrib_pointer: unsafe extern "system" fn(u32, i32, u32, u8, i32, *const c_void),
    get_uniform_location: unsafe extern "system" fn(u32, *const i8) -> i32,
    uniform1i: unsafe extern "system" fn(i32, i32),
    uniform1f: unsafe extern "system" fn(i32, f32),
    uniform3f: unsafe extern "system" fn(i32, f32, f32, f32),
    draw_arrays: unsafe extern "system" fn(u32, i32, i32),
    viewport: unsafe extern "system" fn(i32, i32, i32, i32),
    scissor: unsafe extern "system" fn(i32, i32, i32, i32),
    bind_framebuffer: unsafe extern "system" fn(u32, u32),
    disable: unsafe extern "system" fn(u32),
    enable: unsafe extern "system" fn(u32),
    is_enabled: unsafe extern "system" fn(u32) -> u8,
    blend_func: unsafe extern "system" fn(u32, u32),
    blend_func_separate: unsafe extern "system" fn(u32, u32, u32, u32),
    depth_func: unsafe extern "system" fn(u32),
    active_texture: unsafe extern "system" fn(u32),
    get_integerv: unsafe extern "system" fn(u32, *mut i32),
    get_floatv: unsafe extern "system" fn(u32, *mut f32),
    delete_shader: unsafe extern "system" fn(u32),
    get_error: unsafe extern "system" fn() -> u32,
    depth_mask: unsafe extern "system" fn(u8),
    color_mask: unsafe extern "system" fn(u8, u8, u8, u8),
    get_booleanv: unsafe extern "system" fn(u32, *mut u8),
}

struct GlBlit {
    fns: GlFns,
    dx_device: HANDLE,
    dx_object: HANDLE,
    gl_tex: u32,
    tex_target: u32,
    program: u32,
    vao: u32,
    vbo: u32,
    uses_wgl: bool,
    needs_copy: bool,
    mem_object: u32,
    wgl_lock: Option<WglDxLockObjectsNv>,
    wgl_unlock: Option<WglDxUnlockObjectsNv>,
    tex_loc: i32,
    depth_loc: i32,
    cover_loc: i32,
    clear_loc: i32,
}

pub struct GpuShare {
    d3d11_device: ID3D11Device,
    d3d11_context: ID3D11DeviceContext,
    d3d11_tex: ID3D11Texture2D,
    d3d11_tex_gl: ID3D11Texture2D,
    d3d12_queue: ID3D12CommandQueue,
    fence: ID3D12Fence,
    fence_event: HANDLE,
    fence_value: u64,
    nt_handle: HANDLE,
    alloc_size: u64,
    gl: Option<GlBlit>,
    width: u32,
    height: u32,
    wgpu_format: wgpu::TextureFormat,
}

fn opengl32_module() -> Option<windows::Win32::Foundation::HMODULE> {
    thread_local! {
        static MODULE: std::cell::Cell<Option<isize>> = const { std::cell::Cell::new(None) };
    }
    MODULE.with(|slot| {
        if let Some(raw) = slot.get() {
            return Some(windows::Win32::Foundation::HMODULE(raw as *mut c_void));
        }
        let loaded = unsafe { LoadLibraryA(PCSTR::from_raw(b"opengl32.dll\0".as_ptr())).ok()? };
        slot.set(Some(loaded.0 as isize));
        Some(loaded)
    })
}

fn load_wgl(name: &str) -> *const c_void {
    let c_name = CString::new(name).unwrap();
    unsafe {
        if let Some(p) = wglGetProcAddress(PCSTR(c_name.as_ptr() as *const u8)) {
            let addr = p as *const () as usize;
            if addr > 3 {
                return p as *const c_void;
            }
        }
        if let Some(module) = opengl32_module() {
            if let Some(p) = GetProcAddress(module, PCSTR(c_name.as_ptr() as *const u8)) {
                return p as *const c_void;
            }
        }
        ptr::null()
    }
}

unsafe fn load_fn<T>(name: &str) -> Result<T, String> {
    let p = load_wgl(name);
    if p.is_null() {
        return Err(format!("Missing GL function {name}"));
    }
    Ok(transmute_copy(&p))
}

fn transmute_copy<T>(p: &*const c_void) -> T {
    unsafe { std::ptr::read(p as *const *const c_void as *const T) }
}

fn wgpu_format(dxgi: DXGI_FORMAT) -> wgpu::TextureFormat {
    if dxgi == DXGI_FORMAT_B8G8R8A8_UNORM {
        wgpu::TextureFormat::Bgra8Unorm
    } else {
        wgpu::TextureFormat::Rgba8Unorm
    }
}

impl GpuShare {
    pub fn wgpu_format(&self) -> wgpu::TextureFormat {
        self.wgpu_format
    }

    pub fn create(
        adapter: &wgpu::Adapter,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        width: u32,
        height: u32,
    ) -> Result<(wgpu::Texture, Self), String> {
        unsafe {
            match Self::create_with_format(
                adapter,
                device,
                queue,
                width,
                height,
                DXGI_FORMAT_R8G8B8A8_UNORM,
            ) {
                Ok(ok) => Ok(ok),
                Err(rgba_err) => Self::create_with_format(
                    adapter,
                    device,
                    queue,
                    width,
                    height,
                    DXGI_FORMAT_B8G8R8A8_UNORM,
                )
                .map_err(|bgra_err| format!("RGBA share: {rgba_err}; BGRA share: {bgra_err}")),
            }
        }
    }

    unsafe fn create_with_format(
        adapter: &wgpu::Adapter,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        width: u32,
        height: u32,
        format: DXGI_FORMAT,
    ) -> Result<(wgpu::Texture, Self), String> {
        let hal_adapter = adapter
            .as_hal::<Dx12>()
            .ok_or_else(|| "wgpu adapter is not DX12".to_string())?;
        let dxgi_adapter = hal_adapter.as_raw().clone();
        drop(hal_adapter);

        let d3d12_device = device
            .as_hal::<Dx12>()
            .ok_or_else(|| "wgpu device is not DX12".to_string())?
            .raw_device()
            .clone();
        let d3d12_queue = queue
            .as_hal::<Dx12>()
            .ok_or_else(|| "wgpu queue is not DX12".to_string())?
            .as_raw()
            .clone();

        let dxgi_base: IDXGIAdapter = dxgi_adapter
            .cast()
            .map_err(|e| format!("IDXGIAdapter cast failed: {e}"))?;

        let mut d3d11_device: Option<ID3D11Device> = None;
        let feature_levels = [D3D_FEATURE_LEVEL_11_0];
        D3D11CreateDevice(
            Some(&dxgi_base),
            D3D_DRIVER_TYPE_UNKNOWN,
            Default::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(&feature_levels),
            D3D11_SDK_VERSION,
            Some(&mut d3d11_device),
            None,
            None,
        )
        .map_err(|e| format!("D3D11CreateDevice failed: {e}"))?;
        let d3d11_device =
            d3d11_device.ok_or_else(|| "D3D11CreateDevice returned null".to_string())?;
        let d3d11_context = d3d11_device
            .GetImmediateContext()
            .map_err(|e| format!("D3D11 GetImmediateContext failed: {e}"))?;

        let gpu_desc = D3D11_TEXTURE2D_DESC {
            Width: width,
            Height: height,
            MipLevels: 1,
            ArraySize: 1,
            Format: format,
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: (D3D11_BIND_RENDER_TARGET.0 | D3D11_BIND_SHADER_RESOURCE.0) as u32,
            CPUAccessFlags: 0,
            MiscFlags: (D3D11_RESOURCE_MISC_SHARED.0 | D3D11_RESOURCE_MISC_SHARED_NTHANDLE.0)
                as u32,
        };
        let mut d3d11_tex = None;
        d3d11_device
            .CreateTexture2D(&gpu_desc, None, Some(&mut d3d11_tex))
            .map_err(|e| format!("CreateTexture2D (NT share) failed: {e}"))?;
        let d3d11_tex = d3d11_tex.ok_or_else(|| "CreateTexture2D returned null".to_string())?;

        let mut gl_desc = gpu_desc;
        gl_desc.MiscFlags = D3D11_RESOURCE_MISC_SHARED.0 as u32;
        let mut d3d11_tex_gl = None;
        d3d11_device
            .CreateTexture2D(&gl_desc, None, Some(&mut d3d11_tex_gl))
            .map_err(|e| format!("CreateTexture2D (WGL share) failed: {e}"))?;
        let d3d11_tex_gl =
            d3d11_tex_gl.ok_or_else(|| "CreateTexture2D (WGL) returned null".to_string())?;

        let dxgi_res: IDXGIResource1 = d3d11_tex
            .cast()
            .map_err(|e| format!("IDXGIResource1 cast failed: {e}"))?;
        let nt_handle = match dxgi_res.CreateSharedHandle(
            None,
            DXGI_SHARED_RESOURCE_READ.0 | DXGI_SHARED_RESOURCE_WRITE.0,
            None,
        ) {
            Ok(handle) => handle,
            Err(_) => dxgi_res
                .CreateSharedHandle(None, 0x1000_0000, None)
                .map_err(|e| format!("CreateSharedHandle failed: {e}"))?,
        };

        let mut d3d12_res: Option<ID3D12Resource> = None;
        d3d12_device
            .OpenSharedHandle(nt_handle, &mut d3d12_res)
            .map_err(|e| format!("D3D12 OpenSharedHandle failed: {e}"))?;
        let d3d12_res = d3d12_res.ok_or_else(|| "OpenSharedHandle returned null".to_string())?;
        let alloc_size = d3d12_device
            .GetResourceAllocationInfo(0, &[d3d12_res.GetDesc()])
            .SizeInBytes
            .max(1);

        let wgpu_format = wgpu_format(format);
        let wgpu_desc = wgpu::TextureDescriptor {
            label: Some("ssbh_blender_gpu_share"),
            size: wgpu::Extent3d {
                width,
                height,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu_format,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        };
        let hal_tex = wgpu::hal::dx12::Device::texture_from_raw(
            d3d12_res,
            wgpu_desc.format,
            wgpu_desc.dimension,
            wgpu_desc.size,
            1,
            1,
        );
        let wgpu_texture = device.create_texture_from_hal::<Dx12>(hal_tex, &wgpu_desc);

        let fence = d3d12_device
            .CreateFence(0, windows::Win32::Graphics::Direct3D12::D3D12_FENCE_FLAG_NONE)
            .map_err(|e| format!("CreateFence failed: {e}"))?;
        let fence_event = CreateEventA(None, false, false, PCSTR::null())
            .map_err(|e| format!("CreateEventA failed: {e}"))?;

        Ok((
            wgpu_texture,
            Self {
                d3d11_device,
                d3d11_context,
                d3d11_tex,
                d3d11_tex_gl,
                d3d12_queue,
                fence,
                fence_event,
                fence_value: 0,
                nt_handle,
                alloc_size,
                gl: None,
                width,
                height,
                wgpu_format,
            },
        ))
    }

    pub fn signal(&mut self) -> Result<(), String> {
        self.fence_value += 1;
        unsafe {
            self.d3d12_queue
                .Signal(&self.fence, self.fence_value)
                .map_err(|e| format!("D3D12 Signal failed: {e}"))?;
        }
        Ok(())
    }

    fn wait_gpu(&self) -> Result<(), String> {
        unsafe {
            if self.fence.GetCompletedValue() >= self.fence_value {
                return Ok(());
            }
            self.fence
                .SetEventOnCompletion(self.fence_value, self.fence_event)
                .map_err(|e| format!("SetEventOnCompletion failed: {e}"))?;
            let rc = WaitForSingleObject(self.fence_event, 2000);
            if rc != WAIT_OBJECT_0 {
                return Err("Timed out waiting for Smash GPU frame".into());
            }
        }
        Ok(())
    }

    fn copy_shared_to_gl(&self) {
        unsafe {
            let dest: ID3D11Resource = match self.d3d11_tex_gl.cast() {
                Ok(res) => res,
                Err(_) => return,
            };
            let src: ID3D11Resource = match self.d3d11_tex.cast() {
                Ok(res) => res,
                Err(_) => return,
            };
            self.d3d11_context.CopyResource(&dest, &src);
            self.d3d11_context.Flush();
        }
    }

    pub fn present_gl(
        &mut self,
        dest_w: u32,
        dest_h: u32,
        wait_and_copy: bool,
        cover_grid: bool,
        clear_rgb: [f32; 3],
    ) -> Result<(), String> {
        self.ensure_gl()?;
        if wait_and_copy {
            self.wait_gpu()?;
            if self
                .gl
                .as_ref()
                .map(|gl| gl.needs_copy)
                .unwrap_or(false)
            {
                self.copy_shared_to_gl();
            }
        }
        unsafe { self.present_gl_inner(dest_w, dest_h, cover_grid, clear_rgb) }
    }

    pub fn ensure_gl(&mut self) -> Result<(), String> {
        unsafe {
            if self.gl.is_none() {
                self.gl = Some(self.create_gl_blit()?);
            }
        }
        Ok(())
    }

    unsafe fn present_gl_inner(
        &mut self,
        dest_w: u32,
        dest_h: u32,
        cover_grid: bool,
        clear_rgb: [f32; 3],
    ) -> Result<(), String> {
        let gl = self
            .gl
            .as_mut()
            .ok_or_else(|| "GL interop not initialized".to_string())?;
        let saved = save_gl_target(gl.fns, dest_w.max(1), dest_h.max(1));
        if gl.uses_wgl {
            let mut handle = gl.dx_object;
            let lock = gl.wgl_lock.ok_or_else(|| "wglDXLockObjectsNV missing".to_string())?;
            let unlock = gl
                .wgl_unlock
                .ok_or_else(|| "wglDXUnlockObjectsNV missing".to_string())?;
            if lock(gl.dx_device, 1, &mut handle) == 0 {
                restore_gl_target(gl.fns, &saved);
                return Err("wglDXLockObjectsNV failed".into());
            }
            restore_gl_target(gl.fns, &saved);
            let result = draw_textured_quad(gl, cover_grid, clear_rgb);
            let _ = unlock(gl.dx_device, 1, &mut handle);
            restore_gl_target(gl.fns, &saved);
            result
        } else {
            restore_gl_target(gl.fns, &saved);
            let result = draw_textured_quad(gl, cover_grid, clear_rgb);
            restore_gl_target(gl.fns, &saved);
            result
        }
    }

    unsafe fn create_gl_blit(&self) -> Result<GlBlit, String> {
        let fns = load_gl_fns()?;
        let mut gl_tex = 0u32;
        (fns.gen_textures)(1, &mut gl_tex);

        match self.try_wgl_register(&fns, gl_tex) {
            Ok((dx_device, dx_object, needs_copy, tex_target)) => {
                return finish_gl_blit(
                    fns, gl_tex, tex_target, true, needs_copy, dx_device, dx_object, 0,
                );
            }
            Err(wgl_err) => {
                match self.try_ext_memory(&fns, gl_tex) {
                    Ok(mem_object) => {
                        return finish_gl_blit(
                            fns,
                            gl_tex,
                            GL_TEXTURE_2D,
                            false,
                            false,
                            HANDLE::default(),
                            HANDLE::default(),
                            mem_object,
                        );
                    }
                    Err(ext_err) => {
                        return Err(format!("{wgl_err}; {ext_err}"));
                    }
                }
            }
        }
    }

    unsafe fn try_wgl_register(
        &self,
        fns: &GlFns,
        gl_tex: u32,
    ) -> Result<(HANDLE, HANDLE, bool, u32), String> {
        let open: WglDxOpenDeviceNv = load_fn("wglDXOpenDeviceNV")?;
        let register: WglDxRegisterObjectNv = load_fn("wglDXRegisterObjectNV")?;
        let close: WglDxCloseDeviceNv = load_fn("wglDXCloseDeviceNV")?;

        let dx_device = open(self.d3d11_device.as_raw() as *mut c_void);
        if dx_device.is_invalid() {
            return Err("wglDXOpenDeviceNV failed (need NVIDIA GL/DX interop)".into());
        }

        let candidates: [(&ID3D11Texture2D, bool); 2] = [
            (&self.d3d11_tex, false),
            (&self.d3d11_tex_gl, true),
        ];
        let targets = [GL_TEXTURE_2D, GL_TEXTURE_RECTANGLE];
        let access = [WGL_ACCESS_READ_ONLY_NV, WGL_ACCESS_READ_WRITE_NV];
        let mut last = String::from("wglDXRegisterObjectNV failed");

        for (tex, needs_copy) in candidates {
            for target in targets {
                for acc in access {
                    let _ = (fns.get_error)();
                    SetLastError(windows::Win32::Foundation::WIN32_ERROR(0));
                    let dx_object = register(
                        dx_device,
                        tex.as_raw() as *mut c_void,
                        gl_tex,
                        target,
                        acc,
                    );
                    if !dx_object.is_invalid() {
                        return Ok((dx_device, dx_object, needs_copy, target));
                    }
                    let gle = (fns.get_error)();
                    let win = GetLastError();
                    last = format!(
                        "wglDXRegisterObjectNV failed (GL 0x{gle:x}, win {win:?})"
                    );
                }
            }
        }
        close(dx_device);
        Err(last)
    }

    unsafe fn try_ext_memory(&self, fns: &GlFns, gl_tex: u32) -> Result<u32, String> {
        let import: GlImportMemoryWin32HandleExt = match load_fn("glImportMemoryWin32HandleEXT") {
            Ok(f) => f,
            Err(_) => {
                return Err(
                    "GL_EXT_memory_object_win32 missing; GPU present needs WGL or this extension"
                        .into(),
                );
            }
        };
        let create_mem: GlCreateMemoryObjectsExt = load_fn("glCreateMemoryObjectsEXT")?;
        let storage: GlTexStorageMem2DExt = load_fn("glTexStorageMem2DEXT")?;

        let handle_types = [
            GL_HANDLE_TYPE_D3D11_IMAGE_EXT,
            GL_HANDLE_TYPE_D3D12_RESOURCE_EXT,
        ];
        let internals = [GL_RGBA8, GL_SRGB8_ALPHA8];
        for handle_type in handle_types {
            let mut dup = HANDLE::default();
            if DuplicateHandle(
                GetCurrentProcess(),
                self.nt_handle,
                GetCurrentProcess(),
                &mut dup,
                0,
                false,
                DUPLICATE_SAME_ACCESS,
            )
            .is_err()
            {
                continue;
            }
            let mut mem = 0u32;
            create_mem(1, &mut mem);
            if mem == 0 {
                let _ = CloseHandle(dup);
                continue;
            }
            let _ = (fns.get_error)();
            import(mem, self.alloc_size, handle_type, dup.0);
            if (fns.get_error)() != 0 {
                if let Ok(delete) = load_fn::<GlDeleteMemoryObjectsExt>("glDeleteMemoryObjectsEXT") {
                    delete(1, &mem);
                }
                continue;
            }
            for internal in internals {
                (fns.bind_texture)(GL_TEXTURE_2D, gl_tex);
                let _ = (fns.get_error)();
                storage(
                    GL_TEXTURE_2D,
                    1,
                    internal,
                    self.width as i32,
                    self.height as i32,
                    mem,
                    0,
                );
                if (fns.get_error)() == 0 {
                    (fns.bind_texture)(GL_TEXTURE_2D, 0);
                    return Ok(mem);
                }
            }
            if let Ok(delete) = load_fn::<GlDeleteMemoryObjectsExt>("glDeleteMemoryObjectsEXT") {
                delete(1, &mem);
            }
        }
        Err("glImportMemoryWin32HandleEXT / glTexStorageMem2DEXT failed".into())
    }
}

unsafe fn load_gl_fns() -> Result<GlFns, String> {
    Ok(GlFns {
        create_shader: load_fn("glCreateShader")?,
        shader_source: load_fn("glShaderSource")?,
        compile_shader: load_fn("glCompileShader")?,
        get_shader_iv: load_fn("glGetShaderiv")?,
        create_program: load_fn("glCreateProgram")?,
        attach_shader: load_fn("glAttachShader")?,
        link_program: load_fn("glLinkProgram")?,
        get_program_iv: load_fn("glGetProgramiv")?,
        use_program: load_fn("glUseProgram")?,
        gen_textures: load_fn("glGenTextures")?,
        bind_texture: load_fn("glBindTexture")?,
        tex_parameteri: load_fn("glTexParameteri")?,
        gen_buffers: load_fn("glGenBuffers")?,
        bind_buffer: load_fn("glBindBuffer")?,
        buffer_data: load_fn("glBufferData")?,
        gen_vertex_arrays: load_fn("glGenVertexArrays")?,
        bind_vertex_array: load_fn("glBindVertexArray")?,
        enable_vertex_attrib_array: load_fn("glEnableVertexAttribArray")?,
        vertex_attrib_pointer: load_fn("glVertexAttribPointer")?,
        get_uniform_location: load_fn("glGetUniformLocation")?,
        uniform1i: load_fn("glUniform1i")?,
        uniform1f: load_fn("glUniform1f")?,
        uniform3f: load_fn("glUniform3f")?,
        draw_arrays: load_fn("glDrawArrays")?,
        viewport: load_fn("glViewport")?,
        scissor: load_fn("glScissor")?,
        bind_framebuffer: load_fn("glBindFramebuffer")?,
        disable: load_fn("glDisable")?,
        enable: load_fn("glEnable")?,
        is_enabled: load_fn("glIsEnabled")?,
        blend_func: load_fn("glBlendFunc")?,
        blend_func_separate: load_fn("glBlendFuncSeparate")?,
        depth_func: load_fn("glDepthFunc")?,
        active_texture: load_fn("glActiveTexture")?,
        get_integerv: load_fn("glGetIntegerv")?,
        get_floatv: load_fn("glGetFloatv")?,
        delete_shader: load_fn("glDeleteShader")?,
        get_error: load_fn("glGetError")?,
        depth_mask: load_fn("glDepthMask")?,
        color_mask: load_fn("glColorMask")?,
        get_booleanv: load_fn("glGetBooleanv")?,
    })
}

unsafe fn finish_gl_blit(
    fns: GlFns,
    gl_tex: u32,
    tex_target: u32,
    uses_wgl: bool,
    needs_copy: bool,
    dx_device: HANDLE,
    dx_object: HANDLE,
    mem_object: u32,
) -> Result<GlBlit, String> {
    let (program, vao, vbo) = build_quad_program(&fns, tex_target == GL_TEXTURE_RECTANGLE)?;
    const GL_TEXTURE_MAG_FILTER: u32 = 0x2800;
    const GL_TEXTURE_MIN_FILTER: u32 = 0x2801;
    const GL_NEAREST: i32 = 0x2600;
    (fns.bind_texture)(tex_target, gl_tex);
    (fns.tex_parameteri)(tex_target, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    (fns.tex_parameteri)(tex_target, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    let tex_name = CString::new("u_tex").unwrap();
    let depth_name = CString::new("u_depth").unwrap();
    let cover_name = CString::new("u_cover").unwrap();
    let clear_name = CString::new("u_clear").unwrap();
    let tex_loc = (fns.get_uniform_location)(program, tex_name.as_ptr());
    let depth_loc = (fns.get_uniform_location)(program, depth_name.as_ptr());
    let cover_loc = (fns.get_uniform_location)(program, cover_name.as_ptr());
    let clear_loc = (fns.get_uniform_location)(program, clear_name.as_ptr());
    let (wgl_lock, wgl_unlock) = if uses_wgl {
        (
            Some(load_fn("wglDXLockObjectsNV")?),
            Some(load_fn("wglDXUnlockObjectsNV")?),
        )
    } else {
        (None, None)
    };
    Ok(GlBlit {
        fns,
        dx_device,
        dx_object,
        gl_tex,
        tex_target,
        program,
        vao,
        vbo,
        uses_wgl,
        needs_copy,
        mem_object,
        wgl_lock,
        wgl_unlock,
        tex_loc,
        depth_loc,
        cover_loc,
        clear_loc,
    })
}

unsafe fn compile_shader(fns: &GlFns, kind: u32, src: &[u8]) -> Result<u32, String> {
    let shader = (fns.create_shader)(kind);
    let ptr = src.as_ptr() as *const i8;
    (fns.shader_source)(shader, 1, &ptr, ptr::null());
    (fns.compile_shader)(shader);
    let mut ok = 0;
    (fns.get_shader_iv)(shader, GL_COMPILE_STATUS, &mut ok);
    if ok == 0 {
        return Err("GL shader compile failed".into());
    }
    Ok(shader)
}

fn fragment_src(rect: bool) -> Vec<u8> {
    // Empty Smash clear stays discarded so Blender's backdrop remains.
    // Material/texture alpha is kept. Visible pixels write near depth so
    // the floor cannot draw through the body.
    let body = "void main(){\n\
vec4 t=texture(u_tex,UV);\n\
if(t.a<0.001) discard;\n\
vec3 rgb=t.rgb/max(t.a,0.001);\n\
o_color=vec4(rgb,1.0);\n\
gl_FragDepth=u_depth;\n\
}\n";
    let mut src = if rect {
        format!(
            "#version 330\nuniform sampler2DRect u_tex;\nuniform float u_depth;\nuniform float u_cover;\nuniform vec3 u_clear;\nin vec2 v_uv;\nout vec4 o_color;\n#define UV (v_uv*vec2(textureSize(u_tex)))\n{body}"
        )
    } else {
        format!(
            "#version 330\nuniform sampler2D u_tex;\nuniform float u_depth;\nuniform float u_cover;\nuniform vec3 u_clear;\nin vec2 v_uv;\nout vec4 o_color;\n#define UV v_uv\n{body}"
        )
    };
    src.push('\0');
    src.into_bytes()
}

unsafe fn build_quad_program(fns: &GlFns, rect: bool) -> Result<(u32, u32, u32), String> {
    let vs = compile_shader(
        fns,
        GL_VERTEX_SHADER,
        b"#version 330\nlayout(location=0) in vec2 a_pos;\nlayout(location=1) in vec2 a_uv;\nout vec2 v_uv;\nvoid main(){ v_uv=a_uv; gl_Position=vec4(a_pos,0.0,1.0); }\n\0",
    )?;
    let fs_src = fragment_src(rect);
    let fs = compile_shader(fns, GL_FRAGMENT_SHADER, &fs_src)?;
    let program = (fns.create_program)();
    (fns.attach_shader)(program, vs);
    (fns.attach_shader)(program, fs);
    (fns.link_program)(program);
    let mut ok = 0;
    (fns.get_program_iv)(program, GL_LINK_STATUS, &mut ok);
    (fns.delete_shader)(vs);
    (fns.delete_shader)(fs);
    if ok == 0 {
        return Err("GL program link failed".into());
    }

    let verts: [f32; 16] = [
        -1.0, -1.0, 0.0, 1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0,
    ];
    let mut vao = 0u32;
    let mut vbo = 0u32;
    (fns.gen_vertex_arrays)(1, &mut vao);
    (fns.gen_buffers)(1, &mut vbo);
    (fns.bind_vertex_array)(vao);
    (fns.bind_buffer)(GL_ARRAY_BUFFER, vbo);
    (fns.buffer_data)(
        GL_ARRAY_BUFFER,
        (verts.len() * 4) as isize,
        verts.as_ptr() as *const c_void,
        GL_STATIC_DRAW,
    );
    (fns.enable_vertex_attrib_array)(0);
    (fns.vertex_attrib_pointer)(0, 2, GL_FLOAT, GL_FALSE, 16, ptr::null());
    (fns.enable_vertex_attrib_array)(1);
    (fns.vertex_attrib_pointer)(1, 2, GL_FLOAT, GL_FALSE, 16, 8 as *const c_void);
    (fns.bind_vertex_array)(0);
    Ok((program, vao, vbo))
}

unsafe fn save_gl_target(fns: GlFns, dest_w: u32, dest_h: u32) -> GlTarget {
    const GL_VIEWPORT: u32 = 0x0BA2;
    const GL_SCISSOR_BOX: u32 = 0x0C10;
    const GL_SCISSOR_TEST: u32 = 0x0C11;
    const GL_DRAW_FRAMEBUFFER_BINDING: u32 = 0x8CA6;
    const GL_READ_FRAMEBUFFER_BINDING: u32 = 0x8CAA;
    let mut viewport = [0i32; 4];
    let mut scissor = [0i32; 4];
    let mut draw_fbo = 0i32;
    let mut read_fbo = 0i32;
    (fns.get_integerv)(GL_VIEWPORT, viewport.as_mut_ptr());
    (fns.get_integerv)(GL_SCISSOR_BOX, scissor.as_mut_ptr());
    (fns.get_integerv)(GL_DRAW_FRAMEBUFFER_BINDING, &mut draw_fbo);
    (fns.get_integerv)(GL_READ_FRAMEBUFFER_BINDING, &mut read_fbo);
    if viewport[2] <= 0 || viewport[3] <= 0 {
        viewport = [0, 0, dest_w as i32, dest_h as i32];
    }
    GlTarget {
        viewport,
        scissor,
        scissor_on: (fns.is_enabled)(GL_SCISSOR_TEST) != 0,
        draw_fbo,
        read_fbo,
    }
}

unsafe fn restore_gl_target(fns: GlFns, saved: &GlTarget) {
    const GL_FRAMEBUFFER: u32 = 0x8D40;
    const GL_DRAW_FRAMEBUFFER: u32 = 0x8CA9;
    const GL_READ_FRAMEBUFFER: u32 = 0x8CA8;
    const GL_SCISSOR_TEST: u32 = 0x0C11;
    (fns.bind_framebuffer)(GL_FRAMEBUFFER, saved.draw_fbo as u32);
    (fns.bind_framebuffer)(GL_DRAW_FRAMEBUFFER, saved.draw_fbo as u32);
    (fns.bind_framebuffer)(GL_READ_FRAMEBUFFER, saved.read_fbo as u32);
    (fns.viewport)(
        saved.viewport[0],
        saved.viewport[1],
        saved.viewport[2],
        saved.viewport[3],
    );
    if saved.scissor_on {
        (fns.enable)(GL_SCISSOR_TEST);
    } else {
        (fns.disable)(GL_SCISSOR_TEST);
    }
    (fns.scissor)(
        saved.scissor[0],
        saved.scissor[1],
        saved.scissor[2].max(1),
        saved.scissor[3].max(1),
    );
}

struct GlTarget {
    viewport: [i32; 4],
    scissor: [i32; 4],
    scissor_on: bool,
    draw_fbo: i32,
    read_fbo: i32,
}

unsafe fn draw_textured_quad(
    blit: &GlBlit,
    cover_grid: bool,
    clear_rgb: [f32; 3],
) -> Result<(), String> {
    let fns = blit.fns;
    const GL_CURRENT_PROGRAM: u32 = 0x8B8D;
    const GL_VERTEX_ARRAY_BINDING: u32 = 0x85B5;
    const GL_ARRAY_BUFFER_BINDING: u32 = 0x8894;
    const GL_ACTIVE_TEXTURE: u32 = 0x84E0;
    const GL_TEXTURE0: u32 = 0x84C0;
    const GL_TEXTURE_BINDING_2D: u32 = 0x8069;
    const GL_TEXTURE_BINDING_RECTANGLE: u32 = 0x84F6;
    const GL_DEPTH_WRITEMASK: u32 = 0x0B72;
    const GL_DEPTH_FUNC: u32 = 0x0B74;
    const GL_DEPTH_CLEAR_VALUE: u32 = 0x0B73;
    const GL_COLOR_WRITEMASK: u32 = 0x0C23;
    const GL_BLEND_SRC_RGB: u32 = 0x80C9;
    const GL_BLEND_DST_RGB: u32 = 0x80C8;
    const GL_BLEND_SRC_ALPHA: u32 = 0x80CB;
    const GL_BLEND_DST_ALPHA: u32 = 0x80CA;
    const GL_VIEWPORT: u32 = 0x0BA2;
    const GL_SCISSOR_TEST: u32 = 0x0C11;
    const GL_ALWAYS: u32 = 0x0207;
    const GL_ONE: u32 = 1;
    const GL_ZERO: u32 = 0;
    const GL_ONE_MINUS_SRC_ALPHA: u32 = 0x0303;
    const GL_SAMPLE_ALPHA_TO_COVERAGE: u32 = 0x809E;
    const GL_FRAMEBUFFER_SRGB: u32 = 0x8DB9;

    let mut prev_program = 0;
    let mut prev_vao = 0;
    let mut prev_vbo = 0;
    let mut prev_active = 0;
    let mut prev_tex = 0;
    let mut prev_depth_mask = 1u8;
    let mut prev_depth_func = 0x0201;
    let mut prev_color_mask = [1u8, 1, 1, 1];
    let mut prev_blend = [1i32, 0, 1, 0];
    let mut clear_z = 1.0f32;
    let mut viewport = [0i32; 4];
    (fns.get_integerv)(GL_CURRENT_PROGRAM, &mut prev_program);
    (fns.get_integerv)(GL_VERTEX_ARRAY_BINDING, &mut prev_vao);
    (fns.get_integerv)(GL_ARRAY_BUFFER_BINDING, &mut prev_vbo);
    (fns.get_integerv)(GL_ACTIVE_TEXTURE, &mut prev_active);
    let binding = if blit.tex_target == GL_TEXTURE_RECTANGLE {
        GL_TEXTURE_BINDING_RECTANGLE
    } else {
        GL_TEXTURE_BINDING_2D
    };
    (fns.get_integerv)(binding, &mut prev_tex);
    (fns.get_booleanv)(GL_DEPTH_WRITEMASK, &mut prev_depth_mask);
    (fns.get_integerv)(GL_DEPTH_FUNC, &mut prev_depth_func);
    (fns.get_booleanv)(GL_COLOR_WRITEMASK, prev_color_mask.as_mut_ptr());
    (fns.get_integerv)(GL_BLEND_SRC_RGB, &mut prev_blend[0]);
    (fns.get_integerv)(GL_BLEND_DST_RGB, &mut prev_blend[1]);
    (fns.get_integerv)(GL_BLEND_SRC_ALPHA, &mut prev_blend[2]);
    (fns.get_integerv)(GL_BLEND_DST_ALPHA, &mut prev_blend[3]);
    (fns.get_floatv)(GL_DEPTH_CLEAR_VALUE, &mut clear_z);
    (fns.get_integerv)(GL_VIEWPORT, viewport.as_mut_ptr());
    let depth_on = (fns.is_enabled)(GL_DEPTH_TEST) != 0;
    let blend_on = (fns.is_enabled)(GL_BLEND) != 0;
    let a2c_on = (fns.is_enabled)(GL_SAMPLE_ALPHA_TO_COVERAGE) != 0;
    let srgb_on = (fns.is_enabled)(GL_FRAMEBUFFER_SRGB) != 0;
    // Reverse-Z clears to 0 (near=1). Standard Z clears to 1 (near=0).
    let near_depth = if clear_z < 0.5 { 1.0 } else { 0.0 };

    (fns.enable)(GL_SCISSOR_TEST);
    (fns.scissor)(
        viewport[0],
        viewport[1],
        viewport[2].max(1),
        viewport[3].max(1),
    );
    (fns.color_mask)(1, 1, 1, 1);
    (fns.disable)(GL_FRAMEBUFFER_SRGB);
    (fns.enable)(GL_BLEND);
    (fns.blend_func)(GL_ONE, GL_ZERO);
    (fns.blend_func_separate)(GL_ONE, GL_ZERO, GL_ONE, GL_ZERO);
    (fns.disable)(GL_SAMPLE_ALPHA_TO_COVERAGE);
    (fns.enable)(GL_DEPTH_TEST);
    (fns.depth_func)(GL_ALWAYS);
    (fns.depth_mask)(1);
    (fns.use_program)(blit.program);
    (fns.active_texture)(GL_TEXTURE0);
    (fns.bind_texture)(blit.tex_target, blit.gl_tex);
    if blit.tex_loc >= 0 {
        (fns.uniform1i)(blit.tex_loc, 0);
    }
    if blit.depth_loc >= 0 {
        (fns.uniform1f)(blit.depth_loc, near_depth);
    }
    if blit.cover_loc >= 0 {
        (fns.uniform1f)(blit.cover_loc, if cover_grid { 1.0 } else { 0.0 });
    }
    if blit.clear_loc >= 0 {
        (fns.uniform3f)(
            blit.clear_loc,
            clear_rgb[0],
            clear_rgb[1],
            clear_rgb[2],
        );
    }
    (fns.bind_vertex_array)(blit.vao);
    (fns.draw_arrays)(GL_TRIANGLE_STRIP, 0, 4);

    (fns.bind_vertex_array)(prev_vao as u32);
    (fns.bind_buffer)(GL_ARRAY_BUFFER, prev_vbo as u32);
    (fns.bind_texture)(blit.tex_target, prev_tex as u32);
    (fns.active_texture)(prev_active as u32);
    (fns.use_program)(prev_program as u32);
    (fns.depth_func)(prev_depth_func as u32);
    (fns.depth_mask)(prev_depth_mask);
    (fns.color_mask)(
        prev_color_mask[0],
        prev_color_mask[1],
        prev_color_mask[2],
        prev_color_mask[3],
    );
    (fns.blend_func_separate)(
        prev_blend[0] as u32,
        prev_blend[1] as u32,
        prev_blend[2] as u32,
        prev_blend[3] as u32,
    );
    if !depth_on {
        (fns.disable)(GL_DEPTH_TEST);
    }
    if blend_on {
        (fns.enable)(GL_BLEND);
    } else {
        (fns.disable)(GL_BLEND);
    }
    if a2c_on {
        (fns.enable)(GL_SAMPLE_ALPHA_TO_COVERAGE);
    }
    if srgb_on {
        (fns.enable)(GL_FRAMEBUFFER_SRGB);
    }
    Ok(())
}

impl Drop for GpuShare {
    fn drop(&mut self) {
        if let Some(gl) = self.gl.take() {
            unsafe {
                if gl.uses_wgl {
                    if let Ok(unreg) =
                        load_fn::<WglDxUnregisterObjectNv>("wglDXUnregisterObjectNV")
                    {
                        unreg(gl.dx_device, gl.dx_object);
                    }
                    if let Ok(close) = load_fn::<WglDxCloseDeviceNv>("wglDXCloseDeviceNV") {
                        close(gl.dx_device);
                    }
                }
                if gl.mem_object != 0 {
                    if let Ok(delete) =
                        load_fn::<GlDeleteMemoryObjectsExt>("glDeleteMemoryObjectsEXT")
                    {
                        delete(1, &gl.mem_object);
                    }
                }
            }
        }
        unsafe {
            if !self.nt_handle.is_invalid() {
                let _ = CloseHandle(self.nt_handle);
            }
            let _ = CloseHandle(self.fence_event);
        }
    }
}
