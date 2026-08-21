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
    return this.manifest.models.map(model => {
      const targetPath = path.join(this.modelsDir, model.local_dir.replace(/^mlx_models\/?/, ''));
      let isDownloaded = false;
      try {
        if (fs.existsSync(targetPath)) {
          const files = fs.readdirSync(targetPath);
          isDownloaded = files.length > 0;
        }
      } catch (e) {
        isDownloaded = false;
      }
      return {
        ...model,
        isDownloaded,
        targetPath
      };
    });
  }

  downloadModel(modelId, onProgress, onComplete, onError) {
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

    this.currentProcess.on('close', (code) => {
      this.currentProcess = null;
      if (code === 0) {
        if (onComplete) onComplete(model);
      } else {
        if (onError) onError(new Error(`Download process exited with code ${code}`));
      }
    });
  }

  cancelDownload() {
    if (this.currentProcess) {
      this.currentProcess.kill('SIGTERM');
      this.currentProcess = null;
    }
  }
}

module.exports = ModelDownloader;
