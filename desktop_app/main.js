const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn, execSync } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');
const ModelDownloader = require('./downloader');

let mainWindow = null;
let pyProcess = null;
let startedByUs = false;

// Resolve directories dynamically
const projectRoot = path.resolve(__dirname, '..');
const defaultModelsDir = fs.existsSync(path.join(projectRoot, 'mlx_models'))
  ? path.join(projectRoot, 'mlx_models')
  : path.join(app.getPath('userData'), 'mlx_models');

const pythonBin = fs.existsSync(path.join(projectRoot, 'ltx-2-mlx/env/bin/python3.11'))
  ? path.join(projectRoot, 'ltx-2-mlx/env/bin/python3.11')
  : (fs.existsSync(path.join(projectRoot, 'ltx-2-mlx/env/bin/python3'))
    ? path.join(projectRoot, 'ltx-2-mlx/env/bin/python3')
    : 'python3');

const manifestPath = path.join(__dirname, 'models_manifest.json');
const downloader = new ModelDownloader(defaultModelsDir, manifestPath, pythonBin);

function checkServerReady(cb, retries = 30) {
  http.get('http://127.0.0.1:8198', (res) => {
    cb(true);
  }).on('error', () => {
    if (retries > 0) {
      setTimeout(() => checkServerReady(cb, retries - 1), 500);
    } else {
      cb(false);
    }
  });
}

function startPythonServerIfNeeded(onReady) {
  checkServerReady((alreadyRunning) => {
    if (alreadyRunning) {
      console.log('Phosphene server is already running.');
      onReady();
      return;
    }

    console.log('Starting Phosphene Python server...');
    startedByUs = true;

    const env = Object.assign({}, process.env, {
      LTX_TIER_OVERRIDE: 'base',
      LTX_MODEL: path.join(defaultModelsDir, 'ltx-2.3-mlx-q4'),
      LTX_MODEL_HQ: path.join(defaultModelsDir, 'ltx-2.3-mlx-q8'),
      LTX_GEMMA: path.join(defaultModelsDir, 'gemma-3-12b-it-4bit'),
      LTX_MODELS_DIR: defaultModelsDir,
      LTX_Q8_LOCAL: path.join(defaultModelsDir, 'ltx-2.3-mlx-q8'),
      LTX_HELPER_PYTHON: pythonBin,
      HF_HOME: path.join(projectRoot, 'cache/HF_HOME'),
      PHOSPHENE_SKIP_PREFLIGHT: '1',
      PYTHONPATH: path.join(projectRoot, 'ltx-2-mlx/env/lib/python3.11/site-packages')
    });

    pyProcess = spawn(
      pythonBin,
      ['mlx_ltx_panel.py'],
      {
        cwd: projectRoot,
        env: env,
        stdio: 'ignore'
      }
    );

    checkServerReady((ready) => {
      if (ready) {
        onReady();
      } else {
        console.error('Failed to start Python server in time.');
        onReady();
      }
    });
  });
}

function createMainWindow() {
  const iconPath = path.join(__dirname, 'icon.png');
  if (process.platform === 'darwin' && app.dock && app.dock.setIcon) {
    try { app.dock.setIcon(iconPath); } catch (e) {}
  }

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 720,
    title: 'Phosphene Studio',
    icon: iconPath,
    backgroundColor: '#0b0f19',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  const modelsStatus = downloader.getModelsStatus();
  const requiredMissing = modelsStatus.some(m => m.required && !m.isDownloaded);

  if (requiredMissing) {
    // Show Model Hub UI
    mainWindow.loadFile(path.join(__dirname, 'model_hub.html'));
  } else {
    // Boot server and load app
    startPythonServerIfNeeded(() => {
      mainWindow.loadURL('http://127.0.0.1:8198');
    });
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function stopPythonServer() {
  if (startedByUs && pyProcess) {
    console.log('Stopping Python server...');
    try {
      pyProcess.kill('SIGTERM');
    } catch (e) {}
    try {
      execSync('pkill -f "mlx_ltx_panel.py" || true');
    } catch (e) {}
  }
}

// IPC Communication
ipcMain.on('get-models-status', (event) => {
  event.reply('models-status', downloader.getModelsStatus());
});

ipcMain.on('start-download', (event, modelId) => {
  const model = downloader.manifest.models.find(m => m.id === modelId);
  downloader.downloadModel(
    modelId,
    (log) => {
      if (mainWindow) {
        mainWindow.webContents.send('download-log', log);
        mainWindow.webContents.send('download-progress', {
          modelName: model ? model.name : modelId,
          percent: log.includes('DOWNLOAD_COMPLETE') ? 100 : 50
        });
      }
    },
    () => {
      if (mainWindow) {
        mainWindow.webContents.send('download-complete', modelId);
      }
    },
    (err) => {
      if (mainWindow) {
        mainWindow.webContents.send('download-log', `Error: ${err.message}`);
      }
    }
  );
});

ipcMain.on('start-main-app', () => {
  if (mainWindow) {
    startPythonServerIfNeeded(() => {
      mainWindow.loadURL('http://127.0.0.1:8198');
    });
  }
});

app.whenReady().then(() => {
  createMainWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('window-all-closed', () => {
  stopPythonServer();
  app.quit();
});

app.on('before-quit', () => {
  stopPythonServer();
});
