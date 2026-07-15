from repograte.config import Settings


def test_defaults_are_sane():
    s = Settings(_env_file=None)
    assert s.anthropic_model  # must be non-empty; nodes.py fails hard otherwise
    assert s.max_correction_loops >= 1
    assert s.sandbox_install_cmd
    assert s.sandbox_test_cmd


def test_checkpoint_path_can_be_disabled_for_pure_in_memory_use():
    s = Settings(_env_file=None, checkpoint_path="")
    assert s.checkpoint_path == ""
