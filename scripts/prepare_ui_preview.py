"""Import existing verified media into isolated, explicitly labelled UI preview states.
No media generation or paid provider calls. Never uses an existing production data folder.
"""

from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json, copy, shutil
from video_agent.config import Settings
from video_agent.service import CampaignService
from video_agent.storage import save_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", required=True, type=Path)
parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "ui-preview")
args = parser.parse_args()
root = ROOT
r = json.loads(args.report.read_text())
source = Path(r["data_directory"]) / "campaigns" / r["campaign_id"]
source_brief = json.loads((source / "brief.json").read_text())
manifest = json.loads((source / "assets/asset_manifest.json").read_text())["assets"]
uploads = {
    a["asset_id"]: (a["title"], (source / "assets" / a["file"]).read_bytes())
    for a in manifest
}
target = args.output.resolve()
if (target / "campaigns").exists():
    raise SystemExit(
        "Use a fresh preview output directory; existing data is preserved."
    )
import requests


def no_network(*args, **kwargs):
    raise RuntimeError("UI fixture preparation attempted a provider request.")


requests.sessions.Session.request = no_network
api = CampaignService(Settings(data_dir=target), import_notebook=False)
campaigns = {}
try:
    for label, stage in [
        ("Plan review", "plan"),
        ("Sample review", "sample"),
        ("Final review", "final"),
        ("Export ready", "complete"),
    ]:
        cid = api.create(
            "UI preview · " + label,
            source_brief["audience"],
            source_brief["goal"],
            "template",
            product=source_brief["product"],
            brand=source_brief.get("brand", ""),
            variant=source_brief.get("variant", ""),
            claims=list(source_brief.get("approved_claims", {}).values()),
            cta=source_brief["cta"],
            style=source_brief["style"],
            accent_color=source_brief["accent_color"],
            product_image=uploads["product_main"],
            detail_image=uploads["product_detail"],
        )
        campaigns[stage] = cid
        plan = api.snapshot(cid)["state"]["plan"]
        for i, output in enumerate(r["initial_outputs"]):
            manifest = json.loads((Path(output["folder"]) / "edit_manifest.json").read_text())
            if manifest["timeline"] != plan["ads"][i]:
                raise RuntimeError("Source media does not match a local template for this brief. Use an unmodified verify_local_demo.py run.")
        if stage == "plan":
            continue
        snap = api.snapshot(cid)
        api.act(cid, "approve", snap["signature"])
        api.jobs.wait(cid)
        runtime = api.runtime(cid)
        outputs = []
        folder = api.repository.folder(cid) / "existing-media"
        folder.mkdir(exist_ok=True)
        for i, item in enumerate(r["initial_outputs"]):
            result = copy.deepcopy(item)
            for role in ["video", "captions", "voice", "music", "contact_sheet"]:
                src = Path(item.get(role, ""))
                if src.is_file():
                    dst = folder / (str(i) + "-" + src.name)
                    shutil.copy2(src, dst)
                    result[role] = str(dst)
            outputs.append(result)
        if stage == "sample":
            runtime.agent.update_state(
                runtime.config,
                {"sample": outputs[0], "status": "awaiting_sample_approval"},
                as_node="sample",
            )
        else:
            runtime.agent.update_state(
                runtime.config,
                {
                    "sample": outputs[0],
                    "outputs": outputs,
                    "status": "awaiting_final_approval",
                },
                as_node="batch",
            )
        runtime.agent.invoke(None, runtime.config)
        if stage == "complete":
            snap = api.snapshot(cid)
            api.act(cid, "approve", snap["signature"])
            api.jobs.wait(cid)
    save_json(target / "ui-campaigns.json", campaigns)
    print(json.dumps(campaigns, indent=2))
finally:
    api.close()
