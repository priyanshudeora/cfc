"""JSON-lines speech worker for the separately installed Python 3.12 runtime.

Only local files and CPU inference are used. Stdout is reserved for the protocol.
"""

import contextlib
import os

# Must precede ANY native ORT initialization, including import-time startup.
# disable_telemetry_events() alone cannot suppress the 1DS initialization event.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
import json
from pathlib import Path
import sys
from importlib.metadata import version


def reply(value):
    print(json.dumps(value), flush=True)


def main():
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np
            import onnxruntime as ort

            ort.disable_telemetry_events()
            from kokoro_onnx import Kokoro

            options = ort.SessionOptions()
            options.intra_op_num_threads, options.inter_op_num_threads = 4, 1
            folder = Path(sys.argv[1])
            session = ort.InferenceSession(
                str(folder / "kokoro-v1.0.int8.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            engine = Kokoro.from_session(session, str(folder / "voices-v1.0.bin"))
            # Check the phonemizer/native library as well as model loading.
            samples, rate = engine.create("Ready.", voice="af_heart", lang="en-us")
            if not len(samples) or rate != 24000 or not np.isfinite(samples).all():
                raise ValueError("Voice warm-up returned invalid audio")
        reply(
            dict(
                ok=True,
                voices=engine.get_voices(),
                python=sys.version.split()[0],
                kokoro_onnx=version("kokoro-onnx"),
                onnxruntime=ort.__version__,
                telemetry_opt_out=os.environ["ORT_DISABLE_TELEMETRY"],
            )
        )
    except Exception as error:
        reply(dict(ok=False, error=f"{type(error).__name__}: {error}"))
        return
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("operation") == "close":
                return
            with contextlib.redirect_stdout(sys.stderr):
                samples, rate = engine.create(
                    request["text"],
                    voice=request["voice"],
                    speed=request.get("speed", 1),
                    lang=request.get("lang", "en-us"),
                )
                np.save(request["output"], samples, allow_pickle=False)
            reply(dict(ok=True, rate=rate))
        except Exception as error:
            reply(dict(ok=False, error=f"{type(error).__name__}: {error}"))


if __name__ == "__main__":
    main()
