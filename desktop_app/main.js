const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn, execSync } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');
const ModelDownloader = require('./downloader');

let mainWindow = null;
let pyProcess = null;
let startedByUs = false;
let lastPythonLogs = '';

function getPhospheneRoot() {
  const candidates = [
    '/Users/mk/Phosphene',
    path.join(app.getPath('home'), 'Phosphene'),
    path.resolve(__dirname, '..'),
    __dirname
  ];
  for (const cand of candidates) {
    if (fs.existsSync(path.join(cand, 'mlx_ltx_panel.py')) && fs.existsSync(path.join(cand, 'ltx-2-mlx/env/bin/python3.11'))) {
      return cand;
    }
  }
  for (const cand of candidates) {
    if (fs.existsSync(path.join(cand, 'mlx_ltx_panel.py'))) {
      return cand;
    }
  }
  return '/Users/mk/Phosphene';
}

const projectRoot = getPhospheneRoot();

const defaultModelsDir = fs.existsSync(path.join(projectRoot, 'mlx_models'))
  ? path.join(projectRoot, 'mlx_models')
  : path.join(app.getPath('userData'), 'mlx_models');

function getPythonBin() {
  const envCandidates = [
    path.join(projectRoot, 'ltx-2-mlx/env/bin/python3.11'),
    '/Users/mk/Phosphene/ltx-2-mlx/env/bin/python3.11',
    path.join(projectRoot, 'ltx-2-mlx/env/bin/python3'),
    '/Users/mk/Phosphene/ltx-2-mlx/env/bin/python3',
    path.join(app.getPath('home'), 'Phosphene/ltx-2-mlx/env/bin/python3.11')
  ];
  for (const cand of envCandidates) {
    if (fs.existsSync(cand)) {
      return cand;
    }
  }
  return 'python3';
}

function getDyldLibraryPath() {
  const uvBase = path.join(app.getPath('home'), '.local/share/uv/python');
  let libPaths = [];
  try {
    if (fs.existsSync(uvBase)) {
      const dirs = fs.readdirSync(uvBase);
      for (const d of dirs) {
        const libDir = path.join(uvBase, d, 'lib');
        if (fs.existsSync(libDir)) {
          libPaths.push(libDir);
        }
      }
    }
  } catch (e) {}
  if (process.env.DYLD_LIBRARY_PATH) {
    libPaths.push(process.env.DYLD_LIBRARY_PATH);
  }
  return libPaths.join(':');
}

const pythonBin = getPythonBin();
const manifestPath = path.join(__dirname, 'models_manifest.json');
const downloader = new ModelDownloader(defaultModelsDir, manifestPath, pythonBin);

function checkServerReady(cb, retries = 180) {
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
      console.log('Phosphene server is already running on port 8198.');
      onReady();
      return;
    }

    if (mainWindow) {
      mainWindow.loadFile(path.join(__dirname, 'error.html'));
    }

    const scriptPath = path.join(projectRoot, 'mlx_ltx_panel.py');
    if (!fs.existsSync(scriptPath)) {
      console.error(`Cannot find mlx_ltx_panel.py at ${scriptPath}`);
      if (mainWindow) {
        mainWindow.webContents.send('server-log', `Error: mlx_ltx_panel.py not found at ${scriptPath}`);
      }
      return;
    }

    console.log('Starting Phosphene Python server...');
    startedByUs = true;
    lastPythonLogs = `Starting Python server process using ${pythonBin} at ${projectRoot}...\n`;
    if (mainWindow) {
      mainWindow.webContents.send('server-log', lastPythonLogs);
    }

    const pythonPathEnv = [
      path.join(projectRoot, 'ltx-2-mlx/env/lib/python3.11/site-packages'),
      '/Users/mk/Phosphene/ltx-2-mlx/env/lib/python3.11/site-packages',
      projectRoot,
      '/Users/mk/Phosphene'
    ].join(':');

    const extendedPath = [
      '/opt/homebrew/bin',
      '/usr/local/bin',
      '/usr/bin',
      '/bin',
      '/usr/sbin',
      '/sbin',
      path.join(app.getPath('home'), '.local/bin')
    ].join(':');

    const env = Object.assign({}, process.env, {
      PATH: process.env.PATH ? `${process.env.PATH}:${extendedPath}` : extendedPath,
      DYLD_LIBRARY_PATH: getDyldLibraryPath(),
      PYTHONUNBUFFERED: '1',
      LTX_TIER_OVERRIDE: 'base',
      LTX_MODEL: path.join(defaultModelsDir, 'ltx-2.3-mlx-q4'),
      LTX_MODEL_HQ: path.join(defaultModelsDir, 'ltx-2.3-mlx-q8'),
      LTX_GEMMA: path.join(defaultModelsDir, 'gemma-3-12b-it-4bit'),
      LTX_MODELS_DIR: defaultModelsDir,
      LTX_Q8_LOCAL: path.join(defaultModelsDir, 'ltx-2.3-mlx-q8'),
      LTX_HELPER_PYTHON: pythonBin,
      HF_HOME: path.join(projectRoot, 'cache/HF_HOME'),
      PHOSPHENE_SKIP_PREFLIGHT: '1',
      PYTHONPATH: pythonPathEnv
    });

    try {
      pyProcess = spawn(
        pythonBin,
        [scriptPath],
        {
          cwd: projectRoot,
          env: env,
          stdio: ['ignore', 'pipe', 'pipe']
        }
      );

      pyProcess.stdout.on('data', (data) => {
        const msg = data.toString();
        console.log(`[Python stdout]: ${msg}`);
        lastPythonLogs += msg;
        if (mainWindow) {
          mainWindow.webContents.send('server-log', lastPythonLogs);
        }
      });

      pyProcess.stderr.on('data', (data) => {
        const msg = data.toString();
        console.log(`[Python stderr]: ${msg}`);
        lastPythonLogs += msg;
        if (mainWindow) {
          mainWindow.webContents.send('server-log', lastPythonLogs);
        }
      });

      pyProcess.on('error', (err) => {
        lastPythonLogs += `\n[Process Error]: ${err.message}`;
        if (mainWindow) {
          mainWindow.webContents.send('server-log', lastPythonLogs);
        }
      });

      pyProcess.on('exit', (code, signal) => {
        if (code !== 0 && code !== null) {
          lastPythonLogs += `\n[Process Exited]: Code ${code}, Signal ${signal}`;
          if (mainWindow) {
            mainWindow.webContents.send('server-log', lastPythonLogs);
          }
        }
      });
    } catch (err) {
      lastPythonLogs += `\nSpawn Error: ${err.message}`;
      if (mainWindow) {
        mainWindow.webContents.send('server-log', lastPythonLogs);
      }
    }

    checkServerReady((ready) => {
      if (ready) {
        onReady();
      } else {
        console.error('Server boot timeout.');
        if (mainWindow) {
          mainWindow.webContents.send('server-log', lastPythonLogs + '\n[Error]: Server boot timeout on http://127.0.0.1:8198');
        }
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
  const hasAnyModel = modelsStatus.some(m => m.isDownloaded);

  // If at least 1 model exists or is downloading, go straight to Phosphene Studio!
  if (!hasAnyModel) {
    mainWindow.loadFile(path.join(__dirname, 'model_hub.html'));
  } else {
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

ipcMain.on('get-server-logs', (event) => {
  event.reply('server-log', lastPythonLogs || 'Initializing server logs...');
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
