from tracemind.retrieval.models import EMBEDDING_REVISION, BGEModels


def test_pinned_model_uses_complete_local_snapshot_and_fetches_missing_weights(
    tmp_path, monkeypatch
):
    import huggingface_hub

    for name in ("config.json", "tokenizer_config.json", "tokenizer.json", "pytorch_model.bin"):
        (tmp_path / name).touch()
    calls = []

    def snapshot_download(**options):
        calls.append(options)
        return str(tmp_path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    assert BGEModels()._path("BAAI/bge-m3", EMBEDDING_REVISION) == str(tmp_path)
    assert [call.get("local_files_only", False) for call in calls] == [True]
    (tmp_path / "pytorch_model.bin").unlink()
    BGEModels()._path("BAAI/bge-m3", EMBEDDING_REVISION)
    assert [call.get("local_files_only", False) for call in calls] == [True, True, False]
    assert all(call["revision"] == EMBEDDING_REVISION for call in calls)
