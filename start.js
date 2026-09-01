module.exports = {
  daemon: true,
  run: [{
    method: "shell.run",
    params: {
      // Run the panel from the ltx-2-mlx venv so its stdlib + the helper share
      // one Python install. Panel itself only needs stdlib; the helper subprocess
      // also uses this venv (see LTX_HELPER_PYTHON below).
      venv: "ltx-2-mlx/env",
      env: {
        // Point the panel at the locally-installed weights — never the HF
        // repo id, otherwise the helper triggers a duplicate cache download
        // on first generation even though we just downloaded the model.
        LTX_MODEL: "{{cwd}}/mlx_models/ltx-2.3-mlx-q4",
        LTX_MODEL_HQ: "{{cwd}}/mlx_models/ltx-2.3-mlx-q8",
        LTX_GEMMA: "{{cwd}}/mlx_models/gemma-3-12b-it-4bit",
        LTX_MODELS_DIR: "{{cwd}}/mlx_models",
        LTX_Q8_LOCAL: "{{cwd}}/mlx_models/ltx-2.3-mlx-q8",
        LTX_HELPER_PYTHON: "{{cwd}}/ltx-2-mlx/env/bin/python3.11",
        // HF_HOME tells the panel + helper + mflux subprocesses to use
        // the Pinokio-managed cache at <install>/cache/HF_HOME instead
        // of ~/.cache/huggingface. install_qwen.js + other Pinokio
        // installers download weights into <install>/cache/HF_HOME/hub/,
        // but without this env the panel reads ~/.cache/huggingface/hub/
        // and reports the weights as missing — the "Qwen models
        // downloaded but engine reports not cached / 'mflux-generate-
        // qwen-edit not found'" symptom Mr Bizarro hit.
        //
        // THIS USED TO SAY "shell.run replaces the env wholesale". It does not,
        // and the correction is load-bearing for everything below — read
        // pinokiod/kernel/shell.js init_env(), which builds the child env in
        // this order, each step overwriting the last PER KEY:
        //     process.env
        //  -> <pinokio-home>/ENVIRONMENT      (the global file)
        //  -> <install>/ENVIRONMENT           (the app's own file)
        //  -> this `env` block                (params.env, and it WINS)
        // So a key we do NOT declare here still reaches the panel from either
        // ENVIRONMENT file, and a key we DO declare cannot be overridden from
        // them. Declaring is a decision to take the choice away, not a default.
        //
        // HF_HOME is declared anyway, on purpose: both ENVIRONMENT files ship
        // it as the RELATIVE `./cache/HF_HOME`, and environment.js resolves a
        // leading `./` against the PINOKIO HOME dir — so inherited it points at
        // ~/pinokio/cache/HF_HOME while every Phosphene installer writes into
        // <install>/cache/HF_HOME. Same string, two different folders; the
        // panel reported the weights missing. {{cwd}} settles it.
        HF_HOME: "{{cwd}}/cache/HF_HOME",
        // 2026-05-28 — SSL cert chain for the uv-installed Python (issue #15).
        // Pinokio installs Python via uv into its own cache. That Python
        // does NOT inherit macOS system certificates, so urllib's default
        // SSL context fails with `CERTIFICATE_VERIFY_FAILED` the first time
        // any code path hits HTTPS through stdlib — e.g. the CivitAI LoRA
        // browser in the Image LoRA panel. `certifi` is installed and
        // current in the venv, but stdlib only consults it when these env
        // vars point it at certifi's bundle. Diagnosis + fix by
        // @PiotrAstroCamp in #15 — thanks.
        SSL_CERT_FILE:      "{{cwd}}/ltx-2-mlx/env/lib/python3.11/site-packages/certifi/cacert.pem",
        REQUESTS_CA_BUNDLE: "{{cwd}}/ltx-2-mlx/env/lib/python3.11/site-packages/certifi/cacert.pem",
        // ---- THE H3 OVERRIDES ARE DELIBERATELY NOT DECLARED HERE ------------
        //   LTX_H3_ROOT         default <install>/minimax-h3-mlx
        //   LTX_H3_MODELS       default <install>/mlx_models/hailuo-h3
        //   LTX_H3_COMPACT_DIR  default "ddalcu-q8" (a DIRNAME under the models
        //                       root, not a path)
        // All three are read by mlx_ltx_panel.py from its own process env, and
        // docs/H3_ENGINE.md tells users to set them in <install>/ENVIRONMENT so
        // a second install can point at an existing 75 GB checkout instead of
        // downloading it twice. Per the ordering above, that file is merged into
        // the child env and reaches the panel already — declaring these keys
        // here with their defaults would OVERWRITE the user's ENVIRONMENT and
        // break the only documented way to relocate the H3 tree. Listing the
        // names in a comment is the documentation; declaring them is the bug.
        // Pinokio's menu reads the same two roots out of the same file so the
        // sidebar agrees with the panel — see readEnvironment() in pinokio.js.
      },
      message: ["python mlx_ltx_panel.py"],
      // SHIP-BLOCKER history: we used to have extra `/errno/i` and `/error:/i`
      // patterns here (cargo-culted from comfy.git's start.js) with break:false
      // intended to just keep the shell running through Python tracebacks. But
      // Pinokio's event handler invokes downstream lookups on EVERY matched
      // event — when the panel ever logged a line containing "Errno" (Python's
      // `OSError [Errno N]` format), Pinokio tried to stat a file literally
      // named "Errno" inside the install dir and surfaced
      //   "ENOENT: no such file or directory, stat '.../phosphene.git/Errno'"
      // even though clicking Start was the trigger, not a real error from us.
      // Removed those patterns. We only need the URL match to advance to step 2.
      // Capture group `(http://...)` mirrors comfy.git's working pattern so
      // input.event[1] is the URL (full line is event[0]).
      on: [
        { event: "/(http:\\/\\/[a-zA-Z0-9.]+:[0-9]+)/", done: true }
      ]
    }
  }, {
    method: "local.set",
    params: { url: "{{input.event[1]}}" }
  }]
}
