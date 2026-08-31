"""#1228: ROCm hosts were force-routed to CPU on *every* AMD GPU.

The SM-arch compatibility gate compared a CUDA-namespace tag (``sm_115``,
derived from ``get_device_capability()``) against ``torch.cuda.get_arch_list()``
— which on a ROCm wheel returns **gfx names** (``gfx1100``, ``gfx1201``, …).
The two namespaces can never intersect, so ``check_device_compatibility()``
returned False for every ROCm build and ``get_best_device()`` silently returned
``"cpu"``. The reporter's Radeon 8060S (Strix Halo, gfx1151) was visible to
torch, reported by ``torch.cuda.is_available()``, and still ran on the CPU.

These tests pin the CUDA/ROCm-aware comparison (``core.device_caps
.arch_unsupported``), its three consumers, and the narrowed HSA override.
"""
from __future__ import annotations

import types

import pytest

from core import device_caps
from core.device_caps import KERNEL_RISK_MARKER

# A rocm7.2 wheel's real arch list shape; gfx1151 is natively supported there.
ROCM_ARCHS = ["gfx900", "gfx906", "gfx90a", "gfx942", "gfx1030",
              "gfx1100", "gfx1101", "gfx1102", "gfx1151", "gfx1200"]


def _torch(*, hip=None, capability=(11, 5), gcn_arch="gfx1151:xnack-",
           arch_list=None, device_name="Radeon 8060S Graphics",
           cuda_available=True):
    """Minimal torch mock: CUDA build when ``hip`` is None, else a ROCm build."""
    cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available,
        device_count=lambda: 1,
        get_device_name=lambda i=0: device_name,
        mem_get_info=lambda: (4 * 1024 ** 3, 8 * 1024 ** 3),
        get_device_capability=lambda i=0: capability,
        get_device_properties=lambda i=0: types.SimpleNamespace(gcnArchName=gcn_arch),
        get_arch_list=lambda: list(arch_list if arch_list is not None else []),
    )
    version = types.SimpleNamespace()
    if hip is not None:
        version.hip = hip
    return types.SimpleNamespace(
        cuda=cuda,
        version=version,
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: False)
        ),
    )


@pytest.fixture(autouse=True)
def _no_hsa_override(monkeypatch):
    # Bind the real cached probe up front — a test may monkeypatch the module
    # attribute, and this fixture's teardown runs before monkeypatch's undo.
    clear = device_caps.detect_host_caps.cache_clear
    monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION", raising=False)
    clear()
    yield
    clear()


# ── the comparison itself ────────────────────────────────────────────────


def test_rocm_gfx_in_build_is_supported():
    """The regression: sm_115 vs a gfx list used to read as a mismatch."""
    torch = _torch(hip="7.2.4", arch_list=ROCM_ARCHS)
    assert device_caps.arch_unsupported(torch) is None


def test_rocm_feature_suffixes_are_ignored():
    """``gfx90a:xnack+`` on either side must still match ``gfx90a``."""
    torch = _torch(hip="6.2", capability=(9, 0), gcn_arch="gfx90a:sramecc+:xnack-",
                   arch_list=["gfx908", "gfx90a:xnack+"])
    assert device_caps.arch_unsupported(torch) is None


def test_rocm_genuine_mismatch_still_detected():
    """A real ROCm arch mismatch must not be papered over by the fix."""
    torch = _torch(hip="6.2", capability=(10, 1), gcn_arch="gfx1010",
                   arch_list=["gfx1030", "gfx1100"])
    assert device_caps.arch_unsupported(torch) == ("gfx1010", ("gfx1030", "gfx1100"))


def test_rocm_gpu_we_can_remap_is_not_a_mismatch():
    """gfx1102 is absent from this build but _configure_rocm_if_needed() remaps
    it to gfx1100, which this build DOES ship."""
    torch = _torch(hip="6.2", capability=(11, 2), gcn_arch="gfx1102",
                   arch_list=["gfx1030", "gfx1100"])
    assert device_caps.arch_unsupported(torch) is None


def test_a_remap_target_the_build_lacks_is_still_a_mismatch():
    """Review finding (#1230): being IN the override map was treated as proof
    of compatibility. If the wheel ships neither the native arch nor the remap
    target, the override only changes which kernel is missing — the host must
    still fall back to CPU rather than launch into a guaranteed failure."""
    torch = _torch(hip="6.2", capability=(11, 5), gcn_arch="gfx1151",
                   arch_list=["gfx900", "gfx1030"])  # no gfx1100
    assert device_caps.arch_unsupported(torch) == ("gfx1151", ("gfx900", "gfx1030"))


def test_hsa_override_string_is_derived_from_the_target():
    assert device_caps.hsa_override_for("gfx1100") == "11.0.0"
    assert device_caps.hsa_override_for("gfx1030") == "10.3.0"
    assert device_caps.hsa_override_for("gfx906") == "9.0.6"
    assert device_caps.hsa_override_for("gfx900") == "9.0.0"


def test_every_override_target_has_a_valid_hsa_form():
    for source, target in device_caps.ROCM_GFX_OVERRIDES.items():
        assert target.startswith("gfx"), source
        device_caps.hsa_override_for(target)  # must not raise


def test_rocm_user_hsa_override_is_trusted_when_the_build_has_the_target(monkeypatch):
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "11.0.0")
    torch = _torch(hip="6.2", capability=(10, 1), gcn_arch="gfx1010",
                   arch_list=["gfx1030", "gfx1100"])
    assert device_caps.arch_unsupported(torch) is None


def test_a_stale_hsa_override_does_not_buy_a_free_pass(monkeypatch):
    """Review finding (#1228): ANY override was treated as proof of
    compatibility. The reporter had HSA_OVERRIDE_GFX_VERSION=11.0.0 set from
    older advice — if the installed build ships no gfx1100, honouring that
    blindly routes them into kernels that don't exist instead of falling back
    to CPU."""
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "11.0.0")
    torch = _torch(hip="6.2", capability=(11, 5), gcn_arch="gfx1151",
                   arch_list=["gfx900", "gfx1030"])  # no gfx1100
    result = device_caps.arch_unsupported(torch)
    assert result is not None
    assert "gfx1100" in result[0]
    assert "HSA_OVERRIDE_GFX_VERSION=11.0.0" in result[0]


def test_an_unparseable_hsa_override_is_left_alone(monkeypatch):
    """The user asked for something we don't understand; guessing is worse
    than trusting them."""
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "something-custom")
    torch = _torch(hip="6.2", capability=(11, 5), gcn_arch="gfx1151",
                   arch_list=["gfx900"])
    assert device_caps.arch_unsupported(torch) is None


def test_hsa_override_parsing_round_trips():
    for target in ("gfx1100", "gfx1030", "gfx906", "gfx900"):
        assert device_caps.gfx_for_hsa_override(
            device_caps.hsa_override_for(target)
        ) == target
    for junk in ("garbage", "11.0", "", "11.0.0.0", "a.b.c"):
        assert device_caps.gfx_for_hsa_override(junk) is None


def test_cuda_path_unchanged():
    """Blackwell sm_120 on a ≤sm_90 build is still reported unsupported (#756)."""
    torch = _torch(capability=(12, 0), arch_list=["sm_80", "sm_86", "sm_90"])
    assert device_caps.arch_unsupported(torch) == ("sm_120", ("sm_80", "sm_86", "sm_90"))
    ok = _torch(capability=(8, 6), arch_list=["sm_80", "sm_86", "sm_90"])
    assert device_caps.arch_unsupported(ok) is None


def test_compute_tag_still_matches():
    torch = _torch(capability=(12, 0), arch_list=["sm_90", "compute_120"])
    assert device_caps.arch_unsupported(torch) is None


def test_empty_arch_list_and_broken_metadata_fail_open():
    assert device_caps.arch_unsupported(_torch(hip="6.2", arch_list=[])) is None

    broken = _torch(hip="6.2", arch_list=ROCM_ARCHS)
    broken.cuda.get_device_properties = lambda i=0: (_ for _ in ()).throw(
        RuntimeError("HIP driver died")
    )
    assert device_caps.arch_unsupported(broken) is None


# ── consumer 1: the probe's kernel-risk note ─────────────────────────────


def test_probe_emits_no_kernel_risk_note_on_supported_rocm_host(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "torch",
                        _torch(hip="7.2.4", arch_list=ROCM_ARCHS))
    caps = device_caps.refresh()
    assert caps.family == "rocm"
    assert not any(KERNEL_RISK_MARKER in n for n in caps.notes), caps.notes


# ── consumer 2: get_best_device() / check_device_compatibility() ─────────


def test_rocm_host_routes_to_gpu_not_cpu(monkeypatch):
    """End-to-end: the reporter's host must resolve to "cuda", not "cpu"."""
    import services.model_manager as mm

    torch = _torch(hip="7.2.4", arch_list=ROCM_ARCHS)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: types.SimpleNamespace(family="rocm"),
    )
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)

    assert mm.check_device_compatibility() == (True, None)
    assert mm.get_best_device() == "cuda"


def test_rocm_warning_names_the_rocm_remedy(monkeypatch):
    """A genuine ROCm mismatch must not tell the user to install a CUDA wheel."""
    import services.model_manager as mm

    torch = _torch(hip="6.2", capability=(10, 1), gcn_arch="gfx1010",
                   arch_list=["gfx1030", "gfx1100"])
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    compatible, warning = mm.check_device_compatibility()
    assert compatible is False
    assert "HSA_OVERRIDE_GFX_VERSION" in warning
    assert "cu128" not in warning


# ── consumer 3: the HSA override is a fallback, not a rewrite ───────────


def test_native_support_skips_the_hsa_override(monkeypatch):
    """gfx1151 is native on ROCm 7.x — overriding it onto gfx1100 would force a
    supported GPU onto foreign kernels."""
    import services.model_manager as mm

    mm._configure_rocm_if_needed(_torch(hip="7.2.4", arch_list=ROCM_ARCHS))
    assert "HSA_OVERRIDE_GFX_VERSION" not in __import__("os").environ


def test_override_applied_when_build_lacks_the_arch(monkeypatch):
    import os

    import services.model_manager as mm

    mm._configure_rocm_if_needed(
        _torch(hip="6.2", capability=(11, 2), gcn_arch="gfx1102",
               device_name="AMD Radeon RX 7600",
               arch_list=["gfx1030", "gfx1100"])
    )
    assert os.environ.get("HSA_OVERRIDE_GFX_VERSION") == "11.0.0"


def test_no_override_when_the_target_is_also_missing():
    """Review finding (#1230): pointing HSA_OVERRIDE_GFX_VERSION at an arch the
    build doesn't ship is not a fix. Leave it unset so the compatibility check
    reports the real mismatch and the CPU fallback engages."""
    import os

    import services.model_manager as mm

    mm._configure_rocm_if_needed(
        _torch(hip="6.2", capability=(11, 5), gcn_arch="gfx1151",
               device_name="AMD Radeon 8060S", arch_list=["gfx900", "gfx1030"])
    )
    assert "HSA_OVERRIDE_GFX_VERSION" not in os.environ


def test_unknown_arch_metadata_never_triggers_a_remap():
    """Review finding (#1230): an EMPTY arch list means the build's metadata is
    unavailable, not that the GPU is unsupported. Treating unknown as a
    confirmed mismatch would push a natively-supported gfx1151 onto foreign
    gfx1100 kernels. Fail open."""
    import os

    import services.model_manager as mm

    mm._configure_rocm_if_needed(
        _torch(hip="7.2.4", capability=(11, 5), gcn_arch="gfx1151",
               device_name="AMD Radeon 8060S", arch_list=[])
    )
    assert "HSA_OVERRIDE_GFX_VERSION" not in os.environ


def test_nvidia_never_gets_an_hsa_override():
    import os

    import services.model_manager as mm

    mm._configure_rocm_if_needed(
        _torch(capability=(8, 9), device_name="NVIDIA GeForce RTX 4090",
               arch_list=["sm_89"])
    )
    assert "HSA_OVERRIDE_GFX_VERSION" not in os.environ
