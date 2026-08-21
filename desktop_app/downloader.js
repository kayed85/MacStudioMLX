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

    const script = `
import sys
import os
from huggingface_hub import snapshot_download

repo_id = sys.argv[1]
local_dir = sys.argv[2]
print(f"Starting download of {repo_id} to {local_dir}...")
snapshot_download(repo_id=repo_id, local_dir=local_dir, resume_download=True)
print("DOWNLOAD_COMPLETE")
`;

    const args = ['-c', script, model.repo_id, targetDir];
    this.currentProcess = spawn(this.pythonBin, args, {
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
        if (onError) onError(new Error(`Process exited with code ${code}`));
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
