const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

class ModelDownloader {
  constructor(modelsDir, manifestPath, pythonBin = 'python3') {
    this.modelsDir = modelsDir;
    this.manifestPath = manifestPath;
    this.pythonBin = pythonBin;
    this.currentProcess = null;
    this.loadManifest();
  }

  loadManifest() {
    try {
      const data = fs.readFileSync(this.manifestPath, 'utf8');
      this.manifest = JSON.parse(data);
    } catch (e) {
      console.error('Failed to load models_manifest.json:', e);
      this.manifest = { models: [] };
    }
  }

  getModelsStatus() {
    const home = process.env.HOME || '/Users/mk';
    return this.manifest.models.map(model => {
      const relDir = model.local_dir.replace(/^mlx_models\/?/, '');
      const candidates = [
        path.join(this.modelsDir, relDir),
        path.join('/Users/mk/MacStudioMLX/mlx_models', relDir),
        path.join(home, 'Library/Application Support/macstudio-mlx/mlx_models', relDir),
        path.join(home, 'Library/Application Support/phosphene-studio/mlx_models', relDir)
      ];
      let isDownloaded = false;
      let targetPath = candidates[0];
      for (const cand of candidates) {
        try {
          if (fs.existsSync(cand)) {
            const files = fs.readdirSync(cand);
            if (files.length > 0) {
              isDownloaded = true;
              targetPath = cand;
              break;
            }
          }
        } catch (e) {}
      }
      return {
        ...model,
        isDownloaded,
        targetPath
      };
    });
  }

  downloadModel(modelId, onProgress, onComplete, onError) {
    if (this.currentProcess) {
      if (onError) onError(new Error(`Another download is currently active in the background. Please wait for it to complete.`));
      return;
    }

    const model = this.manifest.models.find(m => m.id === modelId);
    if (!model) {
      if (onError) onError(new Error(`Model ${modelId} not found in manifest.`));
      return;
    }

    const targetDir = path.join(this.modelsDir, model.local_dir.replace(/^mlx_models\/?/, ''));
    if (!fs.existsSync(targetDir)) {
      fs.mkdirSync(targetDir, { recursive: true });
    }

    const projectRoot = path.resolve(__dirname, '..');
    const fetchScript = fs.existsSync(path.join(__dirname, 'scripts/fetch_pack_release.py'))
      ? path.join(__dirname, 'scripts/fetch_pack_release.py')
      : path.join(projectRoot, 'scripts/fetch_pack_release.py');

    let hfDownloaderScript = path.join(__dirname, 'hf_downloader.py');

    if (hfDownloaderScript.includes('app.asar')) {
      const unpacked = hfDownloaderScript.replace('app.asar', 'app.asar.unpacked');
      if (fs.existsSync(unpacked)) {
        hfDownloaderScript = unpacked;
      }
    }

    let args = [];
    let cwd = __dirname;

    // Check if this model key is a GitHub release mirrored pack (like q4_25, q8_25, hq_25)
    if (['q4_25', 'q8_25', 'hq_25'].includes(model.key) && fs.existsSync(fetchScript)) {
      args = [fetchScript, '--repo-key', model.key, '--dest', targetDir];
    } else {
      // Use pure stdlib Python HF downloader (no pip / huggingface_hub dependency needed)
      args = [hfDownloaderScript, model.repo_id, targetDir];
    }

    try {
      this.currentProcess = spawn(this.pythonBin, args, {
        cwd: cwd,
        env: Object.assign({}, process.env, { HF_HUB_ENABLE_HF_TRANSFER: '1' })
      });

      this.currentProcess.stdout.on('data', (data) => {
        const str = data.toString();
        console.log(`[Downloader stdout]: ${str}`);
        if (onProgress) onProgress(str);
      });

      this.currentProcess.stderr.on('data', (data) => {
        const str = data.toString();
        console.log(`[Downloader stderr]: ${str}`);
        if (onProgress) onProgress(str);
      });

      this.currentProcess.on('error', (err) => {
        this.currentProcess = null;
        if (onError) onError(err);
      });

      this.currentProcess.on('close', (code) => {
        this.currentProcess = null;
        if (code === 0) {
          if (onComplete) onComplete(model);
        } else {
          if (onError) onError(new Error(`Download process exited with code ${code}`));
        }
      });
    } catch (err) {
      this.currentProcess = null;
      if (onError) onError(err);
    }
  }

  cancelDownload() {
    if (this.currentProcess) {
      try {
        this.currentProcess.kill('SIGTERM');
      } catch (e) {}
      this.currentProcess = null;
    }
  }
}

module.exports = ModelDownloader;
