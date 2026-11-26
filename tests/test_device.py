"""Running the models on a GPU when there is one."""

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from kizami.generate import GenerateError, generate  # noqa: E402
from kizami.generate.align import resolve_device  # noqa: E402


def test_auto_takes_the_gpu_only_when_there_is_one(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert resolve_device("auto") == "cpu"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert resolve_device("auto") == "cuda"


def test_cpu_is_always_there():
    assert resolve_device("cpu") == "cpu"


def test_asking_for_cuda_without_a_gpu_is_an_error(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(ValueError, match="CUDA"):
        resolve_device("cuda")


def test_an_unknown_device_is_an_error():
    with pytest.raises(ValueError, match="auto, cpu or cuda"):
        resolve_device("tpu")


def test_emissions_on_the_cpu_match_the_default(tmp_path):
    pytest.importorskip("transformers")
    from test_dedicated import KO, _tiny_model

    from kizami.generate.align import w2v2_emissions

    _tiny_model(tmp_path / "m", KO)
    x = np.random.default_rng(0).standard_normal(16000 * 2).astype(np.float32)
    assert np.allclose(w2v2_emissions(x, tmp_path / "m"), w2v2_emissions(x, tmp_path / "m", device="cpu"))


def test_generate_refuses_a_gpu_it_does_not_have(tmp_path, monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    (tmp_path / "vocals.wav").write_bytes(b"")
    (tmp_path / "lyrics.txt").write_text("Hi you\n", encoding="utf-8")
    (tmp_path / "mms_fa").mkdir()
    (tmp_path / "mms_fa" / "model.pt").write_bytes(b"")
    with pytest.raises(GenerateError, match="CUDA"):
        generate(tmp_path, model_dir=tmp_path, device="cuda")


def test_cli_and_sweep_take_a_device():
    from kizami.cli import _build_parser
    from kizami.sweep import build_parser

    assert _build_parser().parse_args(["generate", "song"]).device == "auto"
    assert _build_parser().parse_args(["generate", "song", "--device", "cpu"]).device == "cpu"
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["generate", "song", "--device", "tpu"])
    args = build_parser().parse_args(["song", "--model-dir", "m", "--cache", "c", "--device", "cuda"])
    assert args.device == "cuda"
