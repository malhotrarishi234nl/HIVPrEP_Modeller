import json
from pathlib import Path

import pandas as pd

from run_lhs_pipeline import _select_stage_candidates, parse_args, run_pipeline


def test_lhs_pipeline_runs_stages_with_best_fit_handoffs(tmp_path, monkeypatch):
    calls = []
    config = tmp_path / "base.json"
    config.write_text("{}", encoding="utf-8")

    def fake_run_lhs_calibration(args):
        calls.append(args)
        stage_dir = Path(args.output_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        best_config = stage_dir / "lhs_best_fit_rank_001.json"
        best_config.write_text("{}", encoding="utf-8")
        return {
            "best_fit_configs": [str(best_config)],
            "ranked_csv": str(stage_dir / "ranked.csv"),
            "excel_summary": str(stage_dir / "summary.xlsx"),
            "metadata": str(stage_dir / "metadata.json"),
            "normalized_targets": str(stage_dir / "targets.csv"),
        }

    monkeypatch.setattr("run_lhs_pipeline.run_lhs_calibration", fake_run_lhs_calibration)
    args = parse_args(
        [
            "--config", str(config),
            "--n", "10",
            "--seed", "100",
            "--workers", "2",
            "--output-dir", str(tmp_path / "pipeline"),
        ]
    )

    status = run_pipeline(args)

    assert [call.stage for call in calls] == ["backbone", "diagnosis", "art", "joint"]
    assert [call.seed for call in calls] == [100, 101, 102, 103]
    assert calls[0].config == str(config.resolve())
    assert calls[1].config == status["stages"]["backbone"]["best_fit_config"]
    assert calls[2].config == status["stages"]["diagnosis"]["best_fit_config"]
    assert calls[3].config == status["stages"]["art"]["best_fit_config"]
    assert calls[0].skip_het_sensitivity is False
    assert calls[1].skip_het_sensitivity is True
    assert status["status"] == "completed"
    assert status["final_config"] == status["stages"]["joint"]["best_fit_config"]

    status_path = tmp_path / "pipeline" / "lhs_pipeline_status.json"
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["status"] == "completed"


def test_lhs_pipeline_resume_skips_completed_stages(tmp_path, monkeypatch):
    calls = []
    config = tmp_path / "base.json"
    config.write_text("{}", encoding="utf-8")
    pipeline_dir = tmp_path / "pipeline"
    backbone_dir = pipeline_dir / "lhs_backbone"
    backbone_dir.mkdir(parents=True)
    backbone_best = backbone_dir / "lhs_best_fit_rank_001.json"
    backbone_best.write_text("{}", encoding="utf-8")
    status_path = pipeline_dir / "lhs_pipeline_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "initial_config": str(config.resolve()),
                "output_dir": str(pipeline_dir.resolve()),
                "n": 10,
                "seed": 100,
                "workers": 2,
                "stages": {
                    "backbone": {
                        "status": "completed",
                        "best_fit_config": str(backbone_best),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_run_lhs_calibration(args):
        calls.append(args)
        stage_dir = Path(args.output_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        best_config = stage_dir / "lhs_best_fit_rank_001.json"
        best_config.write_text("{}", encoding="utf-8")
        return {
            "best_fit_configs": [str(best_config)],
            "ranked_csv": str(stage_dir / "ranked.csv"),
            "excel_summary": str(stage_dir / "summary.xlsx"),
            "metadata": str(stage_dir / "metadata.json"),
            "normalized_targets": str(stage_dir / "targets.csv"),
        }

    monkeypatch.setattr("run_lhs_pipeline.run_lhs_calibration", fake_run_lhs_calibration)
    args = parse_args(
        [
            "--config", str(config),
            "--n", "10",
            "--seed", "100",
            "--workers", "2",
            "--output-dir", str(pipeline_dir),
            "--resume",
        ]
    )

    status = run_pipeline(args)

    assert [call.stage for call in calls] == ["diagnosis", "art", "joint"]
    assert calls[0].config == str(backbone_best)
    assert status["status"] == "completed"


def test_lhs_pipeline_resume_skips_completed_stage_branches(tmp_path, monkeypatch):
    calls = []
    config = tmp_path / "base.json"
    config.write_text("{}", encoding="utf-8")
    pipeline_dir = tmp_path / "pipeline"
    art_dir = pipeline_dir / "lhs_art"
    joint_branch_001 = pipeline_dir / "lhs_joint" / "branch_001"
    joint_branch_001.mkdir(parents=True)

    art_best_configs = []
    for rank in range(1, 4):
        best = art_dir / f"lhs_best_fit_rank_{rank:03d}.json"
        best.parent.mkdir(parents=True, exist_ok=True)
        best.write_text("{}", encoding="utf-8")
        art_best_configs.append(str(best))

    existing_joint_best = joint_branch_001 / "lhs_best_fit_rank_001.json"
    existing_joint_best.write_text("{}", encoding="utf-8")
    (joint_branch_001 / "lhs_ranked_results_20260521_092025.csv").write_text(
        "draw_id,total_ll\n1,10.0\n",
        encoding="utf-8",
    )

    status_path = pipeline_dir / "lhs_pipeline_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "initial_config": str(config.resolve()),
                "output_dir": str(pipeline_dir.resolve()),
                "n": 10,
                "seed": 100,
                "workers": 2,
                "beam_width": 3,
                "stages": {
                    "backbone": {"status": "completed", "best_fit_configs": [art_best_configs[0]]},
                    "diagnosis": {"status": "completed", "best_fit_configs": [art_best_configs[0]]},
                    "art": {"status": "completed", "best_fit_configs": art_best_configs},
                    "joint": {
                        "status": "running",
                        "input_configs": art_best_configs,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_run_lhs_calibration(args):
        calls.append(args)
        stage_dir = Path(args.output_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        best_config = stage_dir / "lhs_best_fit_rank_001.json"
        best_config.write_text("{}", encoding="utf-8")
        return {
            "best_fit_configs": [str(best_config)],
            "ranked_csv": str(stage_dir / "ranked.csv"),
            "excel_summary": str(stage_dir / "summary.xlsx"),
            "metadata": str(stage_dir / "metadata.json"),
            "normalized_targets": str(stage_dir / "targets.csv"),
            "ranked": pd.DataFrame({"draw_id": [1], "total_ll": [9.0]}),
        }

    monkeypatch.setattr("run_lhs_pipeline.run_lhs_calibration", fake_run_lhs_calibration)
    args = parse_args(
        [
            "--config", str(config),
            "--n", "10",
            "--seed", "100",
            "--workers", "2",
            "--top-n", "3",
            "--beam-width", "3",
            "--output-dir", str(pipeline_dir),
            "--resume",
        ]
    )

    status = run_pipeline(args)

    assert [Path(call.output_dir).name for call in calls] == ["branch_002", "branch_003"]
    assert status["stages"]["joint"]["branches"][0]["ranked_csv"].endswith("lhs_ranked_results_20260521_092025.csv")
    assert status["status"] == "completed"


def test_lhs_pipeline_beam_width_carries_multiple_configs(tmp_path, monkeypatch):
    calls = []
    config = tmp_path / "base.json"
    config.write_text("{}", encoding="utf-8")

    def fake_run_lhs_calibration(args):
        calls.append(args)
        stage_dir = Path(args.output_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        best_paths = []
        for rank in (1, 2):
            best_config = stage_dir / f"lhs_best_fit_rank_{rank:03d}.json"
            best_config.write_text("{}", encoding="utf-8")
            best_paths.append(str(best_config))
        return {
            "best_fit_configs": best_paths,
            "ranked_csv": str(stage_dir / "ranked.csv"),
            "excel_summary": str(stage_dir / "summary.xlsx"),
            "metadata": str(stage_dir / "metadata.json"),
            "normalized_targets": str(stage_dir / "targets.csv"),
            "ranked": pd.DataFrame(
                {
                    "draw_id": [1, 2],
                    "total_ll": [10.0, 9.0],
                }
            ),
        }

    monkeypatch.setattr("run_lhs_pipeline.run_lhs_calibration", fake_run_lhs_calibration)
    args = parse_args(
        [
            "--config", str(config),
            "--n", "10",
            "--seed", "100",
            "--workers", "2",
            "--top-n", "2",
            "--beam-width", "2",
            "--output-dir", str(tmp_path / "pipeline"),
        ]
    )

    status = run_pipeline(args)

    assert len(status["stages"]["backbone"]["best_fit_configs"]) == 2
    assert [call.stage for call in calls] == [
        "backbone",
        "diagnosis",
        "diagnosis",
        "art",
        "art",
        "joint",
        "joint",
    ]
    assert [call.config for call in calls[1:3]] == status["stages"]["backbone"]["best_fit_configs"]
    assert status["stages"]["diagnosis"]["branches"][0]["input_config"] == status["stages"]["backbone"]["best_fit_configs"][0]
    assert status["status"] == "completed"


def test_select_stage_candidates_keeps_reranked_rows_ahead_of_screen_only(tmp_path):
    branch_dir = tmp_path / "branch"
    branch_dir.mkdir()
    paths = []
    for rank in range(1, 4):
        path = branch_dir / f"lhs_best_fit_rank_{rank:03d}.json"
        path.write_text("{}", encoding="utf-8")
        paths.append(str(path))

    result = {
        "best_fit_configs": paths,
        "ranked": pd.DataFrame(
            {
                "draw_id": [1, 2, 3],
                "total_ll": [-5000.0, -1000.0, -900.0],
                "rerank_total_ll_mean": [-5000.0, None, None],
            }
        ),
    }

    selected = _select_stage_candidates([result], beam_width=2)

    assert selected[0] == paths[0]
    assert selected[1] == paths[2]
