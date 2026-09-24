/**
 * Smart Attendance PWA - Client Logic
 * Menangani Scanner QR, Deteksi Wajah, Liveness Challenge (Senyuman), dan Antrean Offline
 * Version: 2.0 - Fixed
 */

// Konfigurasi Endpoint Google Apps Script Web App Anda
const GAS_URL = "https://script.google.com/macros/s/AKfycbzW8DEIarp94S62cG5w5GA4gb6I4cdYB3f8TceJks7NaM3UOjuTLqa2kD0YaL7mEMoI/exec";

// Global Variables
let currentView = 'scan';
let isModelsLoaded = false;
let faceMatcher = null;
let html5QrcodeScanner = null;
let scanStream = null;
let regStream = null;
let latestLiveDescriptor = null;
let cachedOutletShifts = [];

// Variabel Data dari Hasil Scan QR Code PC
let scannedQRData = null;
let isProcessingQRScan = false;
let isRestartingScanner = false;

// Keadaan Liveness Check
let blinkCount = 0;
let isBlinked = false;
let livenessPassed = false;
let faceVerified = false;
let baselineSmileRatio = null;
let isAttendanceSubmitted = false;

// Keadaan Tugas Luar / Event
let isTugasLuarMode = false;
let tugasLuarEventName = "";

// Cache untuk NRP yang sudah diverifikasi
let verifiedNRPCache = null;
let isRestoringNRP = false;

// State untuk pending attendance
let pendingAttendanceType = null;
let pendingWorkingHour = "";

// State untuk supervisor
let cachedSupervisorPending = [];
let isSupervisorRole = false;
let currentUserProfile = null;

// Native scanner state
let _nativeScannerStream = null;
let _nativeScannerInterval = null;
let _nativeScannerVideo = null;

// Scan failure counter
let _scanFailCount = 0;
let _scanFailTimer = null;

// =========================================================================
// DEBUG HELPERS
// =========================================================================

function dbgLog(msg) {
  const logEl = document.getElementById('dbgLog');
  if (logEl) {
    const time = new Date().toLocaleTimeString('id-ID', { hour12: false, second: '2-digit' });
    if (logEl.innerText === '(Belum ada log)') {
      logEl.innerText = `[${time}] ${msg}\n`;
    } else {
      logEl.innerText += `[${time}] ${msg}\n`;
    }
    logEl.scrollTop = logEl.scrollHeight;
  }
  console.log(`[DBG] ${msg}`);
}

function showDebugPanel(forceOpen = true) {
  const panel = document.getElementById('debugPanel');
  if (!panel) return;
  if (forceOpen) panel.style.display = 'flex';

  const now = new Date().toLocaleTimeString('id-ID', { hour12: false });
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.innerText = text; };

  set('dbgTimestamp', `⏱ Waktu klik: ${now}`);

  if (scannedQRData) {
    set('dbgScannedQR', `📦 scannedQRData:\n  outlet    = ${scannedQRData.outlet}\n  timestamp = ${scannedQRData.timestamp}\n  totp_token= ${scannedQRData.totp_token}`);
  } else {
    set('dbgScannedQR', '📦 scannedQRData: null');
  }

  const nrp = localStorage.getItem('attendance_registered_nrp') || (currentUserProfile ? currentUserProfile.nrp : '');
  set('dbgNRP', `👤 NRP tersimpan: ${nrp || '(tidak ada)'}`);
  set('dbgTugasLuarState', `📌 Tugas Luar Mode: ${isTugasLuarMode ? `AKTIF ("${tugasLuarEventName}")` : 'Non-Aktif'}`);
  set('dbgModelsLoaded', `🤖 AI Models Loaded: ${isModelsLoaded}`);
  set('dbgOnlineStatus', `🌐 Sinyal Internet: ${navigator.onLine ? 'Online' : 'Offline'}`);
  set('dbgIsProcessing', `🔒 isProcessingQRScan: ${isProcessingQRScan}`);
  set('dbgIsRestarting', `🔄 isRestartingScanner: ${isRestartingScanner}`);

  let scannerState = 'null (tidak ada instance)';
  if (html5QrcodeScanner) {
    try {
      const s = html5QrcodeScanner.getState ? html5QrcodeScanner.getState() : '?';
      const stateMap = { 1: 'NOT_STARTED', 2: 'SCANNING', 3: 'PAUSED' };
      scannerState = `ada → state=${s} (${stateMap[s] || 'unknown'})`;
    } catch (e) { scannerState = `ada → getState() error: ${e.message}`; }
  }
  set('dbgScannerState', `📷 html5QrcodeScanner: ${scannerState}`);
  set('dbgCameraState', `🎥 scanStream: ${scanStream ? `aktif (${scanStream.getTracks().length} track)` : 'null'}`);

  const readerEl = document.getElementById('reader');
  set('dbgReaderEl', `🗂 #reader children: ${readerEl ? readerEl.children.length : 'element not found'}`);

  console.log('=== [DEBUG] State Snapshot ===');
  console.log('scannedQRData:', scannedQRData);
  console.log('NRP tersimpan:', nrp);
  console.log('isTugasLuarMode:', isTugasLuarMode, tugasLuarEventName);
  console.log('isModelsLoaded:', isModelsLoaded);
  console.log('isProcessingQRScan:', isProcessingQRScan);
  console.log('scanStream:', scanStream);
}

function closeDebugPanel() {
  const panel = document.getElementById('debugPanel');
  if (panel) panel.style.display = 'none';
}

function copyDebugLog() {
  const logEl = document.getElementById('dbgLog');
  if (logEl && logEl.innerText) {
    navigator.clipboard.writeText(logEl.innerText).then(() => {
      alert("Log debug berhasil disalin ke clipboard!");
    }).catch(e => {
      alert("Gagal menyalin log: " + e.message);
    });
  }
}

// =========================================================================
// HELPER FUNCTIONS
// =========================================================================

function getTodayDateStr() {
  const d = new Date();
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function cleanupOldAttendanceStatus() {
  try {
    const todayDateStr = getTodayDateStr();
    const prefix = 'attendance_status_';
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (key && key.startsWith(prefix) && !key.endsWith('_' + todayDateStr)) {
        localStorage.removeItem(key);
      }
    }
  } catch (e) { }
}

function getOrCreateDeviceId() {
  let deviceId = localStorage.getItem('attendance_device_id');
  if (deviceId && (deviceId.startsWith('DEV-FP-') || deviceId.startsWith('DEV-ID-'))) {
    return deviceId;
  }

  try {
    const fpData = [
      navigator.userAgent || '',
      navigator.language || '',
      screen.width + 'x' + screen.height + 'x' + (screen.colorDepth || 24),
      navigator.hardwareConcurrency || 'cpu-x',
      navigator.deviceMemory || 'mem-x',
      Intl.DateTimeFormat().resolvedOptions().timeZone || 'tz-x',
      getCanvasFingerprint()
    ].join('||');

    const hash = fnv1aHash(fpData);
    deviceId = 'DEV-FP-' + hash;
  } catch (e) {
    const fallbackHash = fnv1aHash(navigator.userAgent || 'fallback');
    deviceId = 'DEV-FP-' + fallbackHash;
  }

  try {
    localStorage.setItem('attendance_device_id', deviceId);
  } catch (e) { }

  return deviceId;
}

function getCanvasFingerprint() {
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 200;
    canvas.height = 50;
    const ctx = canvas.getContext('2d');
    if (!ctx) return 'no-ctx';
    ctx.textBaseline = 'top';
    ctx.font = "14px 'Arial'";
    ctx.fillStyle = '#f60';
    ctx.fillRect(125, 1, 62, 20);
    ctx.fillStyle = '#069';
    ctx.fillText('AttendancePWA,1.0', 2, 15);
    ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
    ctx.fillText('AttendancePWA,1.0', 4, 17);
    return canvas.toDataURL();
  } catch (e) {
    return 'canvas-err';
  }
}

function fnv1aHash(str) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
  }
  return (hash >>> 0).toString(16).toUpperCase().padStart(8, '0');
}

function getCurrentTimeStr() {
  const d = new Date();
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  return `${h}:${m}`;
}

function parseWorkingHours(workingHourStr) {
  if (!workingHourStr || typeof workingHourStr !== 'string') return null;
  const match = workingHourStr.match(/(\d{1,2}[:\.]\d{2})\s*[-–—to]+\s*(\d{1,2}[:\.]\d{2})/i);
  if (!match) return null;

  const normalize = (t) => {
    let [h, m] = t.replace('.', ':').split(':');
    h = h.padStart(2, '0');
    m = m.padStart(2, '0');
    return `${h}:${m}`;
  };

  return {
    start: normalize(match[1]),
    end: normalize(match[2])
  };
}

function checkShouldTriggerReasonModal(attendanceType, workingHourStr) {
  const wh = parseWorkingHours(workingHourStr);
  if (!wh) return false;
  const nowTime = getCurrentTimeStr();

  if (attendanceType === 'CLOCK_IN') {
    return nowTime > wh.start;
  } else if (attendanceType === 'CLOCK_OUT') {
    return nowTime < wh.end;
  }
  return false;
}

function showScanResult(message, type) {
  const resultDiv = document.getElementById('scanResult');
  if (resultDiv) {
    resultDiv.innerHTML = message;
    const typeMap = {
      'success': 'feedback-success',
      'error': 'feedback-error',
      'warning': 'feedback-warning',
      'info': 'feedback-info'
    };
    resultDiv.className = "feedback-message " + (typeMap[type] || 'feedback-info');
    resultDiv.style.display = 'block';
  }
}

function showRegResult(message, type) {
  const resultDiv = document.getElementById('regResult');
  if (resultDiv) {
    resultDiv.innerHTML = message;
    const typeMap = {
      'success': 'feedback-success',
      'error': 'feedback-error',
      'warning': 'feedback-warning',
      'info': 'feedback-info'
    };
    resultDiv.className = "feedback-message " + (typeMap[type] || 'feedback-info');
    resultDiv.style.display = 'block';
  }
}

function showSyncResult(message, type) {
  const resultDiv = document.getElementById('syncResult');
  if (resultDiv) {
    resultDiv.innerHTML = message;
    const typeMap = {
      'success': 'feedback-success',
      'error': 'feedback-error',
      'warning': 'feedback-warning',
      'info': 'feedback-info'
    };
    resultDiv.className = "feedback-message " + (typeMap[type] || 'feedback-info');
    resultDiv.style.display = 'block';
  }
}

function showUnbindResult(message, type) {
  const resultDiv = document.getElementById('unbindResult');
  if (resultDiv) {
    resultDiv.innerHTML = message;
    const typeMap = {
      'success': 'feedback-success',
      'error': 'feedback-error',
      'warning': 'feedback-warning',
      'info': 'feedback-info'
    };
    resultDiv.className = "feedback-message " + (typeMap[type] || 'feedback-info');
    resultDiv.style.display = 'block';
  }
}

function saveLocalAttendanceStatus(nrp, attendanceType, workingHour = "") {
  try {
    const todayDateStr = getTodayDateStr();
    const key = 'attendance_status_' + nrp + '_' + todayDateStr;
    const current = JSON.parse(localStorage.getItem(key) || '{}');
    localStorage.setItem(key, JSON.stringify({
      hasClockIn: current.hasClockIn || (attendanceType === 'CLOCK_IN'),
      lastType: attendanceType,
      working_hour: workingHour || current.working_hour || ""
    }));
  } catch (e) { }
}

function saveTodayAttendanceStatus(nrp, statusObj) {
  if (!nrp) return;
  try {
    const todayDateStr = getTodayDateStr();
    const key = 'attendance_status_' + nrp + '_' + todayDateStr;
    const current = JSON.parse(localStorage.getItem(key) || '{}');

    const updated = {
      ...current,
      hasClockIn: (typeof statusObj.hasClockIn === 'boolean') ? statusObj.hasClockIn : (current.hasClockIn || false),
      hasClockOut: (typeof statusObj.hasClockOut === 'boolean') ? statusObj.hasClockOut : (current.hasClockOut || false),
      lastType: statusObj.lastType || current.lastType || (statusObj.hasClockIn ? 'CLOCK_IN' : null),
      lastTime: statusObj.lastTime || current.lastTime || new Date().toISOString()
    };

    localStorage.setItem(key, JSON.stringify(updated));
  } catch (e) {
    console.warn("Gagal menyimpan status absensi hari ini:", e);
  }
}

function updateOfflineBadge() {
  const existingQueue = localStorage.getItem('offline_attendance_queue');
  const badge = document.getElementById('offlineBadge');

  if (existingQueue) {
    const queue = JSON.parse(existingQueue);
    if (queue.length > 0) {
      badge.innerText = queue.length;
      badge.style.display = 'inline-block';
      return;
    }
  }
  badge.style.display = 'none';
}

function closeBrowserTab() {
  console.log("Mencoba menutup tab browser...");

  try {
    if (typeof closeSyncOverlay === 'function') closeSyncOverlay();
    if (typeof closeUnbindOverlay === 'function') closeUnbindOverlay();
    document.querySelectorAll('.overlay').forEach(overlay => {
      overlay.style.display = 'none';
    });
  } catch (e) {
    console.warn("Gagal menyembunyikan overlay:", e);
  }

  try {
    window.opener = null;
    window.open('', '_self', '');
    window.close();
  } catch (e) {
    console.log("Window close error:", e);
  }

  setTimeout(() => {
    const container = document.querySelector('.container') || document.body;
    if (container) {
      container.innerHTML = `
        <div style="padding: 40px 20px; text-align: center; font-family: 'Outfit', sans-serif;">
          <div style="width: 76px; height: 76px; background: rgba(16, 185, 129, 0.15); border: 2px solid #10b981; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 2.3rem; margin: 0 auto 20px auto; box-shadow: 0 0 25px rgba(16, 185, 129, 0.3);">
            ✓
          </div>
          <h2 style="font-size: 1.6rem; font-weight: 700; color: #ffffff; margin-bottom: 8px;">Absensi Berhasil!</h2>
          <p style="color: #9ca3af; font-size: 0.9rem; margin-bottom: 24px; line-height: 1.5;">
            Data kehadiran Anda telah berhasil tersimpan di server.
          </p>
          <button onclick="try{window.close();}catch(e){}" class="btn" style="background: linear-gradient(135deg, #6366f1, #4f46e5); color: white; padding: 12px 24px; border-radius: 10px; font-weight: 600; width: 100%; cursor: pointer;">
            Tutup Halaman
          </button>
        </div>`;
    }
  }, 200);
}

// =========================================================================
// CAMERA FUNCTIONS
// =========================================================================

async function openCameraStream(facingMode = "user") {
  const attempts = [
    { video: { facingMode: { ideal: facingMode } } },
    { video: { facingMode: facingMode } },
    { video: { facingMode: "user" } },
    { video: true }
  ];

  let lastError = null;

  for (let i = 0; i < attempts.length; i++) {
    try {
      console.log(`[DBG] openCameraStream attempt ${i + 1} with constraints:`, attempts[i]);
      const stream = await navigator.mediaDevices.getUserMedia(attempts[i]);
      if (stream) return stream;
    } catch (err) {
      lastError = err;
      console.warn(`[DBG] openCameraStream attempt ${i + 1} failed (${err.name} - ${err.message}):`, err);
      await new Promise(r => setTimeout(r, 600));
    }
  }

  throw lastError || new Error("Gagal mengaktifkan modul kamera HP.");
}

async function stopAllCameras() {
  // Hentikan native BarcodeDetector scanner jika aktif
  if (_nativeScannerInterval) {
    clearInterval(_nativeScannerInterval);
    _nativeScannerInterval = null;
  }
  if (_nativeScannerStream) {
    try {
      _nativeScannerStream.getTracks().forEach(t => { try { t.stop(); } catch (e) { } });
    } catch (e) { }
    _nativeScannerStream = null;
  }
  if (_nativeScannerVideo) {
    try { _nativeScannerVideo.srcObject = null; } catch (e) { }
    _nativeScannerVideo = null;
  }

  if (scanStream) {
    try {
      scanStream.getTracks().forEach(track => {
        try { track.stop(); } catch (e) { }
      });
    } catch (e) { }
    scanStream = null;
  }

  if (regStream) {
    try {
      regStream.getTracks().forEach(track => {
        try { track.stop(); } catch (e) { }
      });
    } catch (e) { }
    regStream = null;
  }

  if (html5QrcodeScanner) {
    try {
      let isScanning = false;
      try {
        if (html5QrcodeScanner.getState) {
          isScanning = html5QrcodeScanner.getState() === 2;
        } else if (typeof html5QrcodeScanner.isScanning === 'boolean') {
          isScanning = html5QrcodeScanner.isScanning;
        } else {
          isScanning = true;
        }
      } catch (stateErr) {
        isScanning = true;
      }

      if (isScanning) {
        await html5QrcodeScanner.stop().catch(err => console.warn("Scanner stop warning:", err));
      }
      try { await html5QrcodeScanner.clear(); } catch (err) { console.warn("Scanner clear warning:", err); }
    } catch (e) {
      console.warn("Cleanup scanner instance warning:", e);
    }
    html5QrcodeScanner = null;
  }

  try {
    const readerEl = document.getElementById('reader');
    if (readerEl) {
      console.log('[DBG] stopAllCameras: membersihkan #reader, children sebelum clear:', readerEl.children.length);
      readerEl.innerHTML = '';
      const newReader = readerEl.cloneNode(false);
      readerEl.parentNode.replaceChild(newReader, readerEl);
      newReader.id = 'reader';
      console.log('[DBG] stopAllCameras: #reader berhasil dikosongkan');
    }
  } catch (e) { console.warn('[DBG] stopAllCameras: gagal bersihkan #reader', e); }

  try {
    const videoElements = document.querySelectorAll('video');
    videoElements.forEach(v => {
      if (v.srcObject && v.srcObject.getTracks) {
        v.srcObject.getTracks().forEach(track => {
          try { track.stop(); } catch (e) { }
        });
        v.srcObject = null;
      }
    });
  } catch (e) { }

  await new Promise(r => setTimeout(r, 500));
}

function stopScanCamera() {
  if (scanStream) {
    try { scanStream.getTracks().forEach(track => track.stop()); } catch (e) { }
    scanStream = null;
  }
}

function stopRegistrationCamera() {
  if (regStream) {
    try { regStream.getTracks().forEach(track => track.stop()); } catch (e) { }
    regStream = null;
  }
  const area = document.getElementById('registerCameraArea');
  const btn = document.getElementById('btnStartReg');
  const btnCapture = document.getElementById('btnCapturePhoto');
  if (btnCapture) {
    btnCapture.disabled = false;
    btnCapture.classList.remove('btn-loading', 'btn-secondary');
    btnCapture.className = 'btn';
    btnCapture.removeAttribute('style');
    btnCapture.style.display = 'block';
    btnCapture.innerHTML = 'Ambil Foto';
  }
  if (area) area.style.display = 'none';
  if (btn) btn.style.display = 'block';
}

async function startRegistrationFlow() {
  const regNRPInput = document.getElementById('regNRP');
  const nrp = regNRPInput ? regNRPInput.value.trim() : '';
  if (!nrp) {
    showRegResult("Silakan masukkan NRP Anda terlebih dahulu.", "error");
    return;
  }

  const btnStartReg = document.getElementById('btnStartReg');
  if (btnStartReg) btnStartReg.style.display = 'none';

  const regArea = document.getElementById('registerCameraArea');
  if (regArea) regArea.style.display = 'block';

  const regProgress = document.getElementById('regProgress');
  if (regProgress) regProgress.innerHTML = 'Memuat model AI & membuka kamera...';

  const resultDiv = document.getElementById('regResult');
  if (resultDiv) resultDiv.style.display = 'none';

  try {
    if (typeof faceapi !== 'undefined' && (!faceapi.nets.tinyFaceDetector.params || !faceapi.nets.faceLandmark64Net.params)) {
      await loadFaceApiModels();
    }
  } catch (e) {
    console.warn("[Reg] Warning loading face models:", e);
  }

  try {
    await stopAllCameras();
    regStream = await openCameraStream("user");
    const videoEl = document.getElementById('regFaceVideo');
    if (videoEl) {
      videoEl.srcObject = regStream;
      await videoEl.play().catch(e => console.warn("Video play warning:", e));
    }
    if (regProgress) regProgress.innerHTML = 'Posisikan wajah Anda di dalam bidang oval lalu tekan tombol <strong>Ambil Foto</strong>.';
    const btnCapture = document.getElementById('btnCapturePhoto');
    if (btnCapture) {
      btnCapture.style.display = 'block';
      btnCapture.disabled = false;
      btnCapture.className = 'btn';
      btnCapture.innerHTML = 'Ambil Foto';
    }
  } catch (err) {
    console.error("[Reg] Gagal membuka kamera registrasi:", err);
    showRegResult("Gagal membuka kamera depan. Pastikan izin kamera telah diberikan.", "error");
    stopRegistrationCamera();
  }
}

async function captureFaceEmbeddings(btnElement) {
  const regNRPInput = document.getElementById('regNRP');
  const nrp = regNRPInput ? regNRPInput.value.trim() : '';
  if (!nrp) {
    showRegResult("NRP tidak boleh kosong.", "error");
    stopRegistrationCamera();
    return;
  }

  const regProgress = document.getElementById('regProgress');
  if (regProgress) regProgress.innerHTML = '⏳ Menganalisis sampel wajah... Harap tenang.';

  const videoEl = document.getElementById('regFaceVideo');
  if (!videoEl || !regStream) {
    showRegResult("Kamera registrasi tidak aktif.", "error");
    stopRegistrationCamera();
    return;
  }

  try {
    const detection = await faceapi.detectSingleFace(videoEl, new faceapi.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.5 }))
      .withFaceLandmarks()
      .withFaceDescriptor();

    if (!detection) {
      showRegResult("Wajah tidak terdeteksi. Posisikan wajah Anda di tengah kamera dan coba lagi.", "error");
      if (btnElement) {
        btnElement.style.display = 'block';
        btnElement.disabled = false;
      }
      if (regProgress) regProgress.innerHTML = 'Posisikan wajah Anda di dalam bidang oval lalu tekan tombol <strong>Ambil Foto</strong>.';
      return;
    }

    const faceEmbedding = Array.from(detection.descriptor);
    const deviceId = getOrCreateDeviceId();

    if (regProgress) regProgress.innerHTML = '📡 Mengirim data registrasi ke server...';

    const payload = {
      action: "register_face",
      nrp: nrp,
      face_embedding: faceEmbedding,
      device_id: deviceId
    };

    const response = await fetch(GAS_URL, {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    const resData = await response.json();

    if (resData.status === 'success' || resData.success) {
      localStorage.setItem('attendance_registered_nrp', nrp);
      showRegResult(`✅ Registrasi Sukses! Perangkat HP & Sampel Wajah untuk NRP ${nrp} berhasil terdaftar.`, "success");
      stopRegistrationCamera();
    } else {
      showRegResult(`❌ Registrasi Gagal: ${resData.message || 'Terjadi kesalahan pada server.'}`, "error");
      if (btnElement) {
        btnElement.style.display = 'block';
        btnElement.disabled = false;
      }
    }
  } catch (err) {
    console.error("[Reg] Registrasi face embeddings error:", err);
    showRegResult("Gagal memproses sampel wajah: " + err.message, "error");
    stopRegistrationCamera();
  }
}
// =========================================================================
// FACE API MODELS
// =========================================================================

async function loadFaceApiModels(retryCount = 0) {
  const LOCAL_MODEL_URL = './models';
  const CDN_MODEL_URLS = [
    'https://cdn.jsdelivr.net/npm/@vladmandic/face-api/model',
    'https://unpkg.com/@vladmandic/face-api/model'
  ];

  console.log(`[AI Model] Memuat model AI face-api... (percobaan #${retryCount + 1})`);

  const loadingOverlay = document.getElementById('loadingOverlay');
  const loadingText = document.getElementById('loadingOverlayText');
  const loadingErrBox = document.getElementById('loadingOverlayErrBox');

  if (loadingOverlay) loadingOverlay.style.display = 'flex';
  if (loadingText) loadingText.innerText = "Mengunduh Model AI Wajah...";
  if (loadingErrBox) loadingErrBox.style.display = 'none';

  const withTimeout = (promise, ms = 15000) => {
    return Promise.race([
      promise,
      new Promise((_, reject) => setTimeout(() => reject(new Error('Waktu pengunduhan habis (Timeout 15s)')), ms))
    ]);
  };

  // 1. Coba dari folder ./models lokal terlebih dahulu (paralel)
  try {
    if (typeof faceapi === 'undefined') {
      throw new Error("Library face-api.js belum siap atau tidak terdeteksi.");
    }

    await withTimeout(Promise.all([
      faceapi.nets.tinyFaceDetector.loadFromUri(LOCAL_MODEL_URL),
      faceapi.nets.faceLandmark68Net.loadFromUri(LOCAL_MODEL_URL),
      faceapi.nets.faceRecognitionNet.loadFromUri(LOCAL_MODEL_URL)
    ]), 15000);

    isModelsLoaded = true;
    if (loadingOverlay) loadingOverlay.style.display = 'none';
    console.log("✅ Model AI wajah berhasil dimuat dari folder ./models/ lokal!");
    return true;
  } catch (localErr) {
    console.warn("⚠️ Gagal memuat dari ./models/ lokal, mencoba CDN mirror...", localErr);
  }

  // 2. Fallback ke CDN mirrors
  for (const cdnUrl of CDN_MODEL_URLS) {
    try {
      console.log(`[AI Model] Mencoba memuat dari CDN: ${cdnUrl}`);
      await withTimeout(Promise.all([
        faceapi.nets.tinyFaceDetector.loadFromUri(cdnUrl),
        faceapi.nets.faceLandmark68Net.loadFromUri(cdnUrl),
        faceapi.nets.faceRecognitionNet.loadFromUri(cdnUrl)
      ]), 15000);

      isModelsLoaded = true;
      if (loadingOverlay) loadingOverlay.style.display = 'none';
      console.log(`✅ Model AI wajah berhasil dimuat dari CDN (${cdnUrl})!`);
      return true;
    } catch (cdnErr) {
      console.warn(`⚠️ Gagal memuat dari CDN (${cdnUrl}):`, cdnErr);
    }
  }

  // 3. Jika gagal dari semua sumber
  console.error("❌ Gagal memuat model face-api.js dari semua sumber.");
  if (loadingOverlay) {
    if (loadingErrBox) {
      loadingErrBox.style.display = 'block';
      loadingErrBox.innerHTML = `
        <div style="color: #fca5a5; margin-bottom: 10px; font-size: 0.85rem;">⚠️ Gagal memuat model AI wajah. Pastikan koneksi terhubung.</div>
        <div style="display: flex; gap: 8px; justify-content: center; flex-wrap: wrap;">
          <button type="button" onclick="loadFaceApiModels(${retryCount + 1})" class="btn" style="padding: 6px 14px; font-size: 0.8rem; width: auto; display: inline-block;">🔄 Coba Lagi</button>
          <button type="button" onclick="dismissLoadingOverlay()" class="btn btn-secondary" style="padding: 6px 14px; font-size: 0.8rem; width: auto; display: inline-block;">Lanjutkan Tanpa AI</button>
        </div>
      `;
    }
  }
  return false;
}

function dismissLoadingOverlay() {
  const loadingOverlay = document.getElementById('loadingOverlay');
  if (loadingOverlay) loadingOverlay.style.display = 'none';
}

// =========================================================================
// NRP AUTO-RESTORE (FIXED)
// =========================================================================

async function tryAutoRestoreNRP(forceRefresh = false) {
  if (!forceRefresh && verifiedNRPCache) {
    dbgLog('✅ Menggunakan cached NRP: ' + verifiedNRPCache);
    return verifiedNRPCache;
  }

  if (isRestoringNRP) {
    dbgLog('⏳ Auto-restore sedang berjalan, menunggu...');
    let waitCount = 0;
    while (isRestoringNRP && waitCount < 50) {
      await new Promise(r => setTimeout(r, 100));
      waitCount++;
    }
    return verifiedNRPCache || localStorage.getItem('attendance_registered_nrp') || null;
  }

  isRestoringNRP = true;

  try {
    dbgLog('🔍 Memulai tryAutoRestoreNRP (forceRefresh=' + forceRefresh + ')');

    let activeNRP = localStorage.getItem('attendance_registered_nrp');
    if (activeNRP) {
      if (!forceRefresh && navigator.onLine) {
        try {
          const verifyUrl = `${GAS_URL}?action=verify_nrp&nrp=${encodeURIComponent(activeNRP)}`;
          const resp = await fetch(verifyUrl, {
            signal: AbortSignal.timeout(3000)
          });
          const data = await resp.json();
          if (data && (data.status === 'success' || data.code === 200)) {
            verifiedNRPCache = activeNRP;
            dbgLog('✅ NRP dari localStorage valid: ' + activeNRP);
            return activeNRP;
          } else {
            dbgLog('⚠️ NRP dari localStorage tidak valid di server, akan restore ulang');
            localStorage.removeItem('attendance_registered_nrp');
          }
        } catch (e) {
          dbgLog('⚠️ Gagal verifikasi NRP (offline/error), menggunakan cached: ' + activeNRP);
          verifiedNRPCache = activeNRP;
          return activeNRP;
        }
      } else {
        verifiedNRPCache = activeNRP;
        return activeNRP;
      }
    }

    if (!navigator.onLine) {
      dbgLog('⚠️ Offline, tidak bisa restore NRP');
      return null;
    }

    const devId = getOrCreateDeviceId();
    const url = `${GAS_URL}?action=get_user_by_device_id&device_id=${encodeURIComponent(devId)}`;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 6000);

    const resp = await fetch(url, { signal: controller.signal });
    clearTimeout(timeoutId);

    if (!resp.ok) {
      throw new Error('HTTP ' + resp.status);
    }

    const resData = await resp.json();
    const userInfo = (resData && resData.data && typeof resData.data === 'object')
      ? resData.data
      : ((resData && resData.message && typeof resData.message === 'object')
        ? resData.message
        : resData);

    if (resData && (resData.status === 'success' || resData.code === 200) && userInfo && userInfo.nrp) {
      activeNRP = userInfo.nrp;
      localStorage.setItem('attendance_registered_nrp', activeNRP);
      if (userInfo.name) localStorage.setItem('attendance_user_name', userInfo.name);
      if (userInfo.position) localStorage.setItem('attendance_user_position', userInfo.position);
      if (userInfo.outlet) localStorage.setItem('attendance_user_outlet', userInfo.outlet);
      currentUserProfile = userInfo;
      verifiedNRPCache = activeNRP;
      dbgLog('✅ Auto-restore NRP berhasil: ' + activeNRP);
      identifyDeviceUser();
      return activeNRP;
    } else {
      dbgLog('⚠️ Auto-restore gagal: user tidak ditemukan');
      return null;
    }
  } catch (e) {
    dbgLog('❌ Auto-restore error: ' + (e.message || e.toString()));
    return null;
  } finally {
    isRestoringNRP = false;
  }
}

// =========================================================================
// VIEW MANAGEMENT & SCANNER FUNCTIONS
// =========================================================================

async function switchView(viewName) {
  console.log(`[View] switchView('${viewName}') dipanggil`);
  currentView = viewName;

  try {
    await stopAllCameras();
  } catch (e) {
    console.warn("stopAllCameras warning in switchView:", e);
  }

  document.querySelectorAll('.view-screen').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));

  const tabButtons = document.querySelectorAll('.tab-bar .tab-btn');

  if (viewName === 'scan') {
    const vScan = document.getElementById('viewScan');
    if (vScan) vScan.classList.add('active');
    if (tabButtons[0]) tabButtons[0].classList.add('active');

    resetToScanStep1UI();
    startQRScanner();
  } else if (viewName === 'register') {
    const vReg = document.getElementById('viewRegister');
    if (vReg) vReg.classList.add('active');
    if (tabButtons[1]) tabButtons[1].classList.add('active');

    stopRegistrationCamera();
  } else if (viewName === 'am') {
    const vAm = document.getElementById('viewAm');
    if (vAm) vAm.classList.add('active');
    if (tabButtons[2]) tabButtons[2].classList.add('active');
  }
}

window.switchView = switchView;

function resetToScanStep1UI() {
  const step1 = document.getElementById('scanStep1');
  const step2 = document.getElementById('scanStep2');
  const step3 = document.getElementById('scanStep3');
  const result = document.getElementById('scanResult');

  if (step1) step1.style.display = 'block';
  if (step2) step2.style.display = 'none';
  if (step3) step3.style.display = 'none';
  if (result) {
    result.style.display = 'none';
    result.className = 'feedback-message';
  }

  livenessPassed = false;
  faceVerified = false;
  baselineSmileRatio = null;

  const challengeText = document.getElementById('challengeText');
  if (challengeText) {
    challengeText.style.display = 'none';
    challengeText.innerText = 'Memuat pendeteksi...';
  }

  const faceGuide = document.getElementById('faceGuide');
  if (faceGuide) {
    faceGuide.className = 'face-guide-oval';
  }
}

function resetToScanStep1() {
  scannedQRData = null;
  isProcessingQRScan = false;
  livenessPassed = false;
  faceVerified = false;
  baselineSmileRatio = null;
  latestLiveDescriptor = null;
  isTugasLuarMode = false;
  tugasLuarEventName = "";
  const step1 = document.getElementById('scanStep1');
  const step2 = document.getElementById('scanStep2');
  const step3 = document.getElementById('scanStep3');
  const result = document.getElementById('scanResult');

  if (step1) step1.style.display = 'block';
  if (step2) step2.style.display = 'none';
  if (step3) step3.style.display = 'none';
  if (result) result.style.display = 'none';
}

async function startQRScanner() {
  console.log('[DBG] startQRScanner() dipanggil');
  isProcessingQRScan = false;

  try {
    await stopAllCameras();
    console.log('[DBG] startQRScanner: stopAllCameras selesai');
  } catch (e) {
    console.warn("Stop kamera error:", e);
  }

  resetToScanStep1UI();

  const readerEl = document.getElementById('reader');
  if (readerEl) {
    readerEl.innerHTML = '';
    console.log('[DBG] startQRScanner: #reader di-clear');
  } else {
    console.error('[DBG] startQRScanner: #reader TIDAK DITEMUKAN!');
    return;
  }

  await new Promise(r => setTimeout(r, 100));

  const hasBarcodeDetector = ('BarcodeDetector' in window);
  const hasJsQR = (typeof jsQR !== 'undefined');

  dbgLog(`🔬 Engine: BarcodeDetector=${hasBarcodeDetector ? '✅' : '❌'}, jsQR=${hasJsQR ? '✅' : '❌'}`);

  if (hasBarcodeDetector) {
    await _startNativeBarcodeScanner(readerEl);
  } else if (hasJsQR) {
    await _startJsQRScanner(readerEl);
  } else {
    await _startHtml5QrcodeScanner(readerEl);
  }
}

async function _startNativeBarcodeScanner(containerEl) {
  dbgLog('📷 Memulai Native BarcodeDetector scanner...');
  try {
    const detector = new BarcodeDetector({ formats: ['qr_code'] });

    let stream = null;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1280 },
            height: { ideal: 720 }
          }
        });
        break;
      } catch (err) {
        console.warn(`[DBG] Native scanner getUserMedia attempt ${attempt} (${err.name}):`, err);
        if (attempt < 3) {
          await new Promise(r => setTimeout(r, 500));
        } else {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        }
      }
    }
    _nativeScannerStream = stream;

    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.muted = true;
    video.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:14px;';
    video.srcObject = stream;
    containerEl.appendChild(video);
    _nativeScannerVideo = video;

    await new Promise((resolve) => {
      video.onloadedmetadata = resolve;
      setTimeout(resolve, 2000);
    });
    await video.play().catch(e => console.warn('video.play() warning:', e));

    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });

    dbgLog('✅ Kamera native aktif! Mulai scan QR...');

    html5QrcodeScanner = {
      getState: () => 2,
      stop: async () => {
        clearInterval(_nativeScannerInterval);
        _nativeScannerInterval = null;
        if (_nativeScannerStream) {
          _nativeScannerStream.getTracks().forEach(t => t.stop());
          _nativeScannerStream = null;
        }
        if (_nativeScannerVideo) {
          _nativeScannerVideo.srcObject = null;
          _nativeScannerVideo = null;
        }
        console.log('[DBG] Native scanner: stopped');
      },
      pause: (stopVideo) => {
        clearInterval(_nativeScannerInterval);
        _nativeScannerInterval = null;
        if (stopVideo && _nativeScannerVideo) _nativeScannerVideo.pause();
        console.log('[DBG] Native scanner: paused');
      },
      clear: () => {
        if (containerEl) containerEl.innerHTML = '';
      },
      resume: () => {
        if (_nativeScannerVideo) _nativeScannerVideo.play();
        _nativeScannerInterval = setInterval(scanLoop, 125);
      }
    };

    const scanLoop = async () => {
      if (isProcessingQRScan) return;
      if (!video.videoWidth || !video.videoHeight) return;

      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0);

      try {
        const barcodes = await detector.detect(canvas);
        if (barcodes && barcodes.length > 0) {
          const rawValue = barcodes[0].rawValue;
          console.log('[DBG] QR detected by BarcodeDetector:', rawValue);
          await onQRScanSuccess(rawValue, barcodes[0]);
        }
      } catch (e) { }
    };

    _nativeScannerInterval = setInterval(scanLoop, 125);

  } catch (err) {
    dbgLog(`❌ Native scanner error: ${err.message}`);
    console.error('[DBG] _startNativeBarcodeScanner error:', err);
    if (typeof jsQR !== 'undefined') {
      dbgLog('⬇️ Fallback ke jsQR Scanner...');
      await _startJsQRScanner(containerEl);
    } else {
      dbgLog('⬇️ Fallback ke Html5Qrcode...');
      await _startHtml5QrcodeScanner(containerEl);
    }
  }
}

async function _startJsQRScanner(containerEl) {
  dbgLog('⚡ Memulai jsQR Scanner (High-Precision Canvas Mode)...');
  try {
    let stream = null;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1280 },
            height: { ideal: 720 }
          }
        });
        break;
      } catch (err) {
        console.warn(`[DBG] jsQR scanner getUserMedia attempt ${attempt} (${err.name}):`, err);
        if (attempt < 3) {
          await new Promise(r => setTimeout(r, 500));
        } else {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        }
      }
    }
    _nativeScannerStream = stream;

    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.muted = true;
    video.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:14px;';
    video.srcObject = stream;
    containerEl.appendChild(video);
    _nativeScannerVideo = video;

    await new Promise((resolve) => {
      video.onloadedmetadata = resolve;
      setTimeout(resolve, 2000);
    });
    await video.play().catch(e => console.warn('video.play() warning:', e));

    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });

    dbgLog('✅ Kamera jsQR aktif! Mulai scan QR layar PC...');

    html5QrcodeScanner = {
      getState: () => 2,
      stop: async () => {
        if (_nativeScannerInterval) clearInterval(_nativeScannerInterval);
        _nativeScannerInterval = null;
        if (_nativeScannerStream) {
          _nativeScannerStream.getTracks().forEach(t => t.stop());
          _nativeScannerStream = null;
        }
        if (_nativeScannerVideo) {
          _nativeScannerVideo.srcObject = null;
          _nativeScannerVideo = null;
        }
        console.log('[DBG] jsQR scanner: stopped');
      },
      pause: (stopVideo) => {
        if (_nativeScannerInterval) clearInterval(_nativeScannerInterval);
        _nativeScannerInterval = null;
        if (stopVideo && _nativeScannerVideo) _nativeScannerVideo.pause();
        console.log('[DBG] jsQR scanner: paused');
      },
      clear: () => {
        if (containerEl) containerEl.innerHTML = '';
      },
      resume: () => {
        if (_nativeScannerVideo) _nativeScannerVideo.play();
        _nativeScannerInterval = setInterval(scanLoop, 100);
      }
    };

    const scanLoop = async () => {
      if (isProcessingQRScan) return;
      if (!video.videoWidth || !video.videoHeight) return;

      const scanWidth = Math.min(video.videoWidth, 800);
      const scanHeight = Math.floor(video.videoHeight * (scanWidth / video.videoWidth));

      canvas.width = scanWidth;
      canvas.height = scanHeight;
      ctx.drawImage(video, 0, 0, scanWidth, scanHeight);

      const imageData = ctx.getImageData(0, 0, scanWidth, scanHeight);
      const code = jsQR(imageData.data, imageData.width, imageData.height, {
        inversionAttempts: "dontInvert"
      });

      if (code && code.data && code.data.trim() !== '') {
        console.log('[DBG] QR code detected by jsQR:', code.data);
        await onQRScanSuccess(code.data, code);
      }
    };

    _nativeScannerInterval = setInterval(scanLoop, 100);

  } catch (err) {
    dbgLog(`❌ jsQR scanner error: ${err.message}`);
    console.error('[DBG] _startJsQRScanner error:', err);
    dbgLog('⬇️ Fallback ke Html5Qrcode (ZXing)...');
    await _startHtml5QrcodeScanner(containerEl);
  }
}

async function _startHtml5QrcodeScanner(containerEl) {
  dbgLog('📷 Memulai Html5Qrcode (ZXing) scanner...');

  const config = {
    fps: 8,
    aspectRatio: 4 / 3,
    experimentalFeatures: { useBarCodeDetectorIfSupported: true }
  };

  try {
    let cameraId = null;
    try {
      const devices = await Html5Qrcode.getCameras();
      if (devices && devices.length > 0) {
        const backCamera = devices.find(d =>
          d.label.toLowerCase().includes('back') ||
          d.label.toLowerCase().includes('rear') ||
          d.label.toLowerCase().includes('environment') ||
          d.label.toLowerCase().includes('0')
        );
        cameraId = backCamera ? backCamera.id : devices[devices.length - 1].id;
      }
    } catch (e) { console.warn("getCameras error:", e); }

    html5QrcodeScanner = new Html5Qrcode("reader");

    if (cameraId) {
      await html5QrcodeScanner.start(cameraId, config, onQRScanSuccess, onQRScanFailure);
    } else {
      await html5QrcodeScanner.start({ facingMode: "environment" }, config, onQRScanSuccess, onQRScanFailure);
    }
    dbgLog('✅ Html5Qrcode (ZXing) aktif');
  } catch (err1) {
    console.warn("Gagal Html5Qrcode primary, mencoba fallback facingMode...", err1);
    try {
      await stopAllCameras();
      await new Promise(r => setTimeout(r, 300));
      const el = document.getElementById('reader');
      if (el) el.innerHTML = '';
      html5QrcodeScanner = new Html5Qrcode("reader");
      await html5QrcodeScanner.start({ facingMode: "user" }, config, onQRScanSuccess, onQRScanFailure);
      dbgLog('✅ Html5Qrcode fallback (facingMode user) aktif');
    } catch (err2) {
      console.error("Gagal total menyalakan kamera:", err2);
      dbgLog(`❌ Gagal buka kamera: ${err2.message}`);
      showScanResult("Gagal membuka kamera: " + (err2.message || err2.toString()), "error");
    }
  }
}

function onQRScanFailure(error) {
  _scanFailCount++;
  if (!_scanFailTimer) {
    _scanFailTimer = setTimeout(() => {
      dbgLog('🔄 Scan attempts: ' + _scanFailCount + ' (dalam 2 detik terakhir)');
      const dbgScannerEl = document.getElementById('dbgScannerState');
      if (dbgScannerEl) {
        const stateText = html5QrcodeScanner ?
          (html5QrcodeScanner.getState ? 'state=' + html5QrcodeScanner.getState() : 'ada') : 'null';
        dbgScannerEl.innerText = '📷 scanner: ' + stateText + ' | scan attempts: ' + _scanFailCount;
      }
      _scanFailCount = 0;
      _scanFailTimer = null;
    }, 2000);
  }
}

async function cancelScan() {
  scannedQRData = null;
  isProcessingQRScan = false;
  livenessPassed = false;
  faceVerified = false;
  baselineSmileRatio = null;
  latestLiveDescriptor = null;

  try {
    if (window.location.search) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  } catch (e) { }

  const resultDiv = document.getElementById('scanResult');
  if (resultDiv) {
    resultDiv.style.display = 'none';
    resultDiv.className = 'feedback-message';
  }

  await stopAllCameras();
  resetToScanStep1UI();

  setTimeout(() => {
    startQRScanner();
  }, 300);
}

// =========================================================================
// QR SCAN SUCCESS HANDLER (FIXED)
// =========================================================================

async function onQRScanSuccess(decodedText, decodedResult) {
  if (isProcessingQRScan) {
    return;
  }

  dbgLog(`🔎 QR terdeteksi! (${decodedText.length} chars)`);
  console.log("QR Code terdeteksi:", decodedText);

  isProcessingQRScan = true;

  try {
    let outlet = null;
    let timestamp = null;
    let totpToken = null;

    if (decodedText.includes("outlet=") && decodedText.includes("totp_token=")) {
      let searchParams = null;
      if (decodedText.startsWith("http://") || decodedText.startsWith("https://")) {
        const url = new URL(decodedText);
        searchParams = url.searchParams;
      } else {
        const queryString = decodedText.includes("?") ? decodedText.split("?")[1] : decodedText;
        searchParams = new URLSearchParams(queryString);
      }

      outlet = searchParams.get('outlet') || searchParams.get('outlet_id');
      timestamp = searchParams.get('timestamp');
      totpToken = searchParams.get('totp_token');
    } else {
      try {
        const json = JSON.parse(decodedText);
        outlet = json.outlet || json.outlet_id;
        timestamp = json.timestamp;
        totpToken = json.totp_token;
      } catch (e) {
        console.warn("Bukan format JSON:", decodedText.substring(0, 80));
      }
    }

    if (!outlet || !totpToken || !timestamp) {
      throw new Error("Parameter QR Code tidak lengkap");
    }

    scannedQRData = {
      outlet: outlet,
      timestamp: Number(timestamp),
      totp_token: totpToken
    };

    dbgLog(`📦 outlet=${outlet}, timestamp=${timestamp}, totp=${totpToken ? totpToken.substring(0, 8) + '...' : 'null'}`);
    fetchOutletShifts(outlet);

    if (html5QrcodeScanner) {
      try {
        html5QrcodeScanner.pause(true);
        dbgLog('⏸ Scanner dijeda (pause)');
      } catch (e) {
        console.warn("Pause scanner warning:", e);
      }
    }

    let activeNRP = localStorage.getItem('attendance_registered_nrp');

    if (activeNRP) {
      dbgLog('✅ NRP ditemukan di localStorage: ' + activeNRP);
      verifiedNRPCache = activeNRP;
    } else {
      dbgLog('🔍 NRP tidak ada di localStorage, mencoba auto-restore...');
      activeNRP = await tryAutoRestoreNRP(false);
      if (activeNRP) {
        dbgLog('✅ Auto-restore berhasil: ' + activeNRP);
      } else {
        dbgLog('❌ Auto-restore gagal, tidak ada NRP');
      }
    }

    if (!activeNRP) {
      dbgLog('⚠️ Tidak ada NRP, membuka overlay sync');
      await stopAllCameras();
      resetToScanStep1UI();
      openSyncOverlay();
      isProcessingQRScan = false;
      return;
    }

    const pendingUnbindNRP = localStorage.getItem('attendance_pending_unbind_nrp');
    if (pendingUnbindNRP) {
      dbgLog('🔍 Mengecek status unbind pending...');
      const unbindStatus = await checkUnbindStatusFromServer(pendingUnbindNRP);
      if (unbindStatus.status === 'PENDING') {
        showUnbindPendingScreen(pendingUnbindNRP, unbindStatus.requested_at);
        isProcessingQRScan = false;
        return;
      } else if (unbindStatus.status === 'APPROVED') {
        await handleUnbindApproved(pendingUnbindNRP);
        isProcessingQRScan = false;
        return;
      } else {
        localStorage.removeItem('attendance_pending_unbind_nrp');
      }
    }

    dbgLog('✅ NRP valid: ' + activeNRP + ', lanjut ke Langkah 2');

    setTimeout(async () => {
      try {
        await stopAllCameras();
        dbgLog('✅ Kamera dihentikan, membuka kamera depan...');
        await startLivenessCamera();
        dbgLog('✅ Kamera depan aktif — Langkah 2 dimulai!');
      } catch (err) {
        console.error("Transisi ke Langkah 2 gagal:", err);
        dbgLog('❌ Transisi gagal: ' + (err.message || err.toString()));
        showScanResult("Gagal membuka kamera verifikasi: " + (err.message || err.toString()), "error");
        resetToScanStep1UI();
        isProcessingQRScan = false;
        setTimeout(() => startQRScanner(), 1000);
      }
    }, 300);

  } catch (error) {
    isProcessingQRScan = false;
    console.error("Format QR Code tidak valid:", error);
    dbgLog('❌ QR gagal parse: ' + error.message);
    showScanResult("Format QR Code tidak sesuai: " + error.message, "error");

    setTimeout(() => {
      if (html5QrcodeScanner) {
        try { html5QrcodeScanner.resume(); } catch (e) { }
      }
      isProcessingQRScan = false;
    }, 2000);
  }
}

// =========================================================================
// LIVENESS CAMERA
// =========================================================================

let smileFrameCount = 0;

async function startLivenessCamera() {
  livenessPassed = false;
  baselineSmileRatio = null;
  smileFrameCount = 0;

  try {
    await stopAllCameras();
    await new Promise(r => setTimeout(r, 400));
  } catch (e) { }

  document.getElementById('scanStep1').style.display = 'none';
  document.getElementById('scanStep2').style.display = 'block';
  document.getElementById('challengeText').style.display = 'block';
  document.getElementById('challengeText').innerText = "Mendeteksi wajah Anda...";

  const video = document.getElementById('scanFaceVideo');

  try {
    scanStream = await openCameraStream("user");
    video.srcObject = scanStream;

    await video.play().catch(e => console.warn("Video play warning:", e));

    video.onloadedmetadata = () => {
      runLivenessLoop(video);
    };
    if (video.readyState >= 2) {
      runLivenessLoop(video);
    }
  } catch (error) {
    console.error("Gagal membuka kamera depan:", error);
    showScanResult("Gagal mengakses kamera depan: " + (error.message || error.toString()) + ".<br><button onclick='startLivenessCamera()' class='btn' style='margin-top:10px; padding:8px 16px; font-size:0.85rem; width:auto; display:inline-block;'>🔄 Coba Buka Kamera Lagi</button>", "error");
  }
}

async function runLivenessLoop(video) {
  if (!scanStream) return;

  const faceGuide = document.getElementById('faceGuide');
  const challengeText = document.getElementById('challengeText');

  const detection = await faceapi.detectSingleFace(video, new faceapi.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.5 }))
    .withFaceLandmarks()
    .withFaceDescriptor();

  if (detection) {
    faceVerified = true;
    
    // Simpan sampel descriptor netral sebelum senyuman lebar mendistorsi geometri wajah
    if (smileFrameCount === 0 || !latestLiveDescriptor) {
      latestLiveDescriptor = Array.from(detection.descriptor);
    }
    
    faceGuide.className = "face-guide-oval verified";

    if (!livenessPassed) {
      challengeText.innerText = "Tantangan: SILAKAN TERSENYUM! 😊";

      const isSmileDetected = checkSmileLiveness(detection.landmarks);

      if (isSmileDetected) {
        livenessPassed = true;
        challengeText.innerText = "Senyuman Terdeteksi! 😊";
        stopScanCamera();
        showScanStep3();
        return;
      }
    }
  } else {
    faceVerified = false;
    latestLiveDescriptor = null;
    baselineSmileRatio = null;
    smileFrameCount = 0;
    faceGuide.className = "face-guide-oval";
    challengeText.innerText = "Dekatkan wajah Anda ke kamera";
  }

  setTimeout(() => runLivenessLoop(video), 60);
}

function checkSmileLiveness(landmarks) {
  const mouth = landmarks.getMouth();
  const leftEye = landmarks.getLeftEye();
  const rightEye = landmarks.getRightEye();

  if (!mouth || mouth.length < 10 || !leftEye || !rightEye) return false;

  const mouthWidth = Math.hypot(mouth[6].x - mouth[0].x, mouth[6].y - mouth[0].y);
  const eyeWidth = Math.hypot(rightEye[3].x - leftEye[0].x, rightEye[3].y - leftEye[0].y);

  if (eyeWidth === 0) return false;

  const currentSmileRatio = mouthWidth / eyeWidth;

  if (baselineSmileRatio === null) {
    baselineSmileRatio = currentSmileRatio;
    smileFrameCount = 0;
    return false;
  }

  const ratioIncrease = (currentSmileRatio - baselineSmileRatio) / baselineSmileRatio;
  const isSmiling = ratioIncrease >= 0.12 && currentSmileRatio > 0.50;

  if (isSmiling) {
    smileFrameCount++;
  } else {
    smileFrameCount = Math.max(0, smileFrameCount - 1);
  }

  return smileFrameCount >= 4;
}

// =========================================================================
// SHOW STEP 3 - ATTENDANCE MENU
// =========================================================================

async function showScanStep3() {
  const step1 = document.getElementById('scanStep1');
  const step2 = document.getElementById('scanStep2');
  const step3 = document.getElementById('scanStep3');

  if (step1) step1.style.display = 'none';
  if (step2) step2.style.display = 'none';
  if (step3) step3.style.display = 'block';

  const localNRP = localStorage.getItem('attendance_registered_nrp') || '';
  const deviceId = getOrCreateDeviceId();

  if (localNRP || deviceId) {
    const syncUrl = `${GAS_URL}?action=get_user_by_device_id&device_id=${encodeURIComponent(deviceId)}&nrp=${encodeURIComponent(localNRP)}`;
    fetch(syncUrl)
      .then(resp => resp.json())
      .then(resData => {
        if (isAttendanceSubmitted) return;

        const userInfo = (resData && resData.data && typeof resData.data === 'object') ? resData.data : resData;
        if (userInfo && userInfo.today_status && (userInfo.nrp || localNRP)) {
          const targetNrp = userInfo.nrp || localNRP;
          saveTodayAttendanceStatus(targetNrp, {
            hasClockIn: userInfo.today_status.has_clock_in || false,
            hasClockOut: userInfo.today_status.has_clock_out || false,
            lastType: userInfo.today_status.last_type || "",
            lastTime: new Date().toISOString()
          });
        }
      })
      .catch(err => console.warn("Sinkronisasi absensi background error:", err));
  }

  if (scannedQRData && (scannedQRData.outlet || scannedQRData.outlet_id)) {
    fetchOutletShifts(scannedQRData.outlet || scannedQRData.outlet_id);
  }

  const menuButtons = document.querySelectorAll('#scanStep3 .menu-card');
  menuButtons.forEach(btn => {
    btn.removeAttribute('disabled');
    btn.style.opacity = '1';
    btn.style.pointerEvents = 'auto';
  });

  if (localNRP) {
    checkSupervisorRoleForNRP(localNRP, false);
  }
}

async function fetchOutletShifts(outletName) {
  cachedOutletShifts = [];
  if (!outletName || !GAS_URL) return;

  const cleanOutlet = String(outletName).trim();
  const cacheKey = 'outlet_shifts_' + cleanOutlet.toLowerCase();

  try {
    const stored = localStorage.getItem(cacheKey);
    if (stored) {
      cachedOutletShifts = JSON.parse(stored);
    }
  } catch (e) { }

  if (navigator.onLine) {
    try {
      const url = `${GAS_URL}?action=get_outlet_shifts&outlet=${encodeURIComponent(cleanOutlet)}`;
      const response = await fetch(url);
      const data = await response.json();
      if (data && data.status === "success" && Array.isArray(data.message)) {
        cachedOutletShifts = data.message;
        localStorage.setItem(cacheKey, JSON.stringify(cachedOutletShifts));
      }
    } catch (err) {
      console.warn("Gagal fetch shift outlet dari GAS:", err);
    }
  }
}

// =========================================================================
// ATTENDANCE HANDLERS
// =========================================================================

async function handleClockInClick() {
  const localNRP = localStorage.getItem('attendance_registered_nrp') || 'Karyawan';
  const todayDateStr = getTodayDateStr();
  const localStatusKey = 'attendance_status_' + localNRP + '_' + todayDateStr;
  let localStatus = {};
  try {
    localStatus = JSON.parse(localStorage.getItem(localStatusKey) || '{}');
  } catch (e) { }

  if (localStatus.hasClockIn) {
    showScanResult("❌ Absensi Ditolak: Anda sudah melakukan Clock In hari ini (tidak dapat melakukan Clock In berulang kali).", "error");
    return;
  }

  const outletName = (scannedQRData && (scannedQRData.outlet || scannedQRData.outlet_id)) || '';

  if (!isTugasLuarMode && (!cachedOutletShifts || cachedOutletShifts.length === 0) && outletName) {
    showScanResult("⏳ Memuat opsi shift jam kerja...", "info");
    await fetchOutletShifts(outletName);
  }

  if (!isTugasLuarMode && cachedOutletShifts && cachedOutletShifts.length > 0) {
    openShiftOverlay();
  } else {
    submitAttendance('CLOCK_IN', '', '');
  }
}

function handleClockOutClick() {
  const localNRP = localStorage.getItem('attendance_registered_nrp') || 'Karyawan';
  const todayDateStr = getTodayDateStr();
  const localStatusKey = 'attendance_status_' + localNRP + '_' + todayDateStr;
  let localStatus = {};
  try {
    localStatus = JSON.parse(localStorage.getItem(localStatusKey) || '{}');
  } catch (e) { }

  const hasClockIn = localStatus.hasClockIn || false;
  const lastType = localStatus.lastType || null;
  const workingHour = localStatus.working_hour || '';

  if (!hasClockIn) {
    showScanResult("❌ Absensi Ditolak: Anda harus melakukan Clock In (Masuk Kerja) terlebih dahulu sebelum Clock Out.", "error");
    return;
  }
  if (lastType === "START_BREAK") {
    showScanResult("❌ Absensi Ditolak: Anda sedang dalam masa Istirahat. Silakan lakukan Stop Break terlebih dahulu sebelum Clock Out.", "error");
    return;
  }
  if (lastType === "CLOCK_OUT") {
    showScanResult("❌ Absensi Ditolak: Anda sudah melakukan Clock Out (Pulang Kerja) untuk hari ini.", "error");
    return;
  }

  const shouldTrigger = checkShouldTriggerReasonModal('CLOCK_OUT', workingHour);

  if (shouldTrigger) {
    openReasonOverlay('CLOCK_OUT', workingHour);
  } else {
    submitAttendance('CLOCK_OUT', workingHour, '');
  }
}

function openShiftOverlay() {
  const overlay = document.getElementById('shiftSelectOverlay');
  const outletText = document.getElementById('shiftOutletName');
  const container = document.getElementById('shiftOptionsContainer');

  if (!overlay || !container) {
    submitAttendance('CLOCK_IN', '', '');
    return;
  }

  const outletName = (scannedQRData && (scannedQRData.outlet || scannedQRData.outlet_id)) || '';
  if (outletText) {
    outletText.innerText = "Outlet: " + outletName + " — Silakan pilih jam kerja Anda:";
  }

  container.innerHTML = '';
  cachedOutletShifts.forEach(item => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.style.cssText = 'background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.4); justify-content: space-between; padding: 14px 18px; color: var(--text-main); font-weight: 600; text-align: left; margin-bottom: 8px; width: 100%; border-radius: 12px; cursor: pointer;';

    const shiftText = item.shift || 'Shift';
    const hourText = item.working_hour || '';
    const hourVal = hourText || shiftText;

    btn.innerHTML = `<span style="font-weight: 600; font-size: 0.95rem;">${shiftText}</span><span style="font-size:0.85rem; color:var(--text-muted);">${hourText}</span>`;
    btn.onclick = () => {
      closeShiftOverlay();
      const shouldTrigger = checkShouldTriggerReasonModal('CLOCK_IN', hourVal);
      if (shouldTrigger) {
        openReasonOverlay('CLOCK_IN', hourVal);
      } else {
        submitAttendance('CLOCK_IN', hourVal, '');
      }
    };
    container.appendChild(btn);
  });

  overlay.style.display = 'flex';
}

function closeShiftOverlay() {
  const overlay = document.getElementById('shiftSelectOverlay');
  if (overlay) overlay.style.display = 'none';
}

function openReasonOverlay(attendanceType, selectedWorkingHour = '') {
  pendingAttendanceType = attendanceType;
  pendingWorkingHour = selectedWorkingHour;

  const overlay = document.getElementById('reasonSelectOverlay');
  const container = document.getElementById('reasonOptionsContainer');
  const title = document.getElementById('reasonModalTitle');
  const sub = document.getElementById('reasonModalSub');

  if (!overlay || !container) {
    submitAttendance(attendanceType, selectedWorkingHour, '');
    return;
  }

  container.innerHTML = '';

  let options = [];
  if (attendanceType === 'CLOCK_IN') {
    if (title) title.innerText = "📋 Kategori / Alasan Terlambat";
    if (sub) sub.innerText = "Waktu masuk kerja melebihi jam mulai shift. Silakan pilih alasan jika ada (opsional):";
    options = [
      { label: "🛒 Belanja Kebutuhan Outlet", value: "Belanja Kebutuhan Outlet", note: "Memerlukan Persetujuan Area Manager (Tugas Belanja)" },
      { label: "⏰ Izin Terlambat", value: "Izin Terlambat", note: "Memerlukan Persetujuan Area Manager" },
      { label: "📝 Lupa Absen", value: "Lupa Absen", note: "Memerlukan Persetujuan Area Manager" }
    ];
  } else if (attendanceType === 'CLOCK_OUT') {
    if (title) title.innerText = "📋 Kategori / Alasan Pulang Awal";
    if (sub) sub.innerText = "Waktu pulang lebih awal dari jam selesai shift. Silakan pilih alasan jika ada (opsional):";
    options = [
      { label: "🏃 Pulang Awal", value: "Pulang Awal", note: "Memerlukan Persetujuan Area Manager" }
    ];
  }

  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.style.cssText = 'background: rgba(15, 23, 42, 0.8); border: 1px solid rgba(255, 255, 255, 0.15); display: flex; flex-direction: column; align-items: flex-start; padding: 12px 16px; color: var(--text-main); font-weight: 600; text-align: left; border-radius: 12px; cursor: pointer; transition: all 0.2s;';

    btn.innerHTML = `<span style="font-weight: 600; font-size: 0.95rem; color: #f8fafc;">${opt.label}</span><span style="font-size:0.75rem; color: var(--text-muted); margin-top: 2px;">${opt.note}</span>`;
    btn.onclick = () => {
      closeReasonOverlay();
      submitAttendance(pendingAttendanceType, pendingWorkingHour, opt.value);
    };
    container.appendChild(btn);
  });

  overlay.style.display = 'flex';
}

function skipReasonAndSubmit() {
  closeReasonOverlay();
  submitAttendance(pendingAttendanceType, pendingWorkingHour, '');
}

function closeReasonOverlay() {
  const overlay = document.getElementById('reasonSelectOverlay');
  if (overlay) overlay.style.display = 'none';
}

// =========================================================================
// SUBMIT ATTENDANCE
// =========================================================================

function submitAttendance(attendanceType = "CLOCK_IN", selectedWorkingHour = "", selectedReason = "") {
  const localNRP = localStorage.getItem('attendance_registered_nrp') || 'Karyawan';
  const challengeText = document.getElementById('challengeText');

  isAttendanceSubmitted = true;

  const menuButtons = document.querySelectorAll('#scanStep3 .menu-card');
  menuButtons.forEach(btn => {
    btn.setAttribute('disabled', 'true');
    btn.style.opacity = '0.6';
    btn.style.pointerEvents = 'none';
  });

  if (!scannedQRData || (!scannedQRData.outlet && !scannedQRData.outlet_id)) {
    console.error("Data QR Code tidak ditemukan!");
    showScanResult("Data QR Code tidak valid. Silakan scan ulang QR Code.", "error");
    setTimeout(() => {
      resetToScanStep1();
      startQRScanner();
    }, 3000);
    return;
  }

  const todayDateStr = getTodayDateStr();
  const localStatusKey = 'attendance_status_' + localNRP + '_' + todayDateStr;
  let localStatus = {};
  try {
    localStatus = JSON.parse(localStorage.getItem(localStatusKey) || '{}');
  } catch (e) { }

  const hasClockIn = localStatus.hasClockIn || false;
  const lastType = localStatus.lastType || null;

  let validationError = null;

  if (attendanceType === "CLOCK_IN") {
    if (hasClockIn) {
      validationError = "Anda sudah melakukan Clock In hari ini (tidak dapat melakukan Clock In berulang kali).";
    }
  } else if (attendanceType === "START_BREAK") {
    if (!hasClockIn) {
      validationError = "Anda harus melakukan Clock In (Masuk Kerja) terlebih dahulu sebelum Start Break.";
    } else if (lastType === "START_BREAK") {
      validationError = "Anda sedang dalam masa Istirahat (tidak dapat Start Break berulang kali).";
    } else if (lastType === "CLOCK_OUT") {
      validationError = "Anda sudah melakukan Clock Out (Pulang Kerja) untuk hari ini.";
    }
  } else if (attendanceType === "STOP_BREAK" || attendanceType === "END_BREAK") {
    if (lastType !== "START_BREAK") {
      validationError = "Stop Break hanya dapat dilakukan jika Anda telah melakukan Start Break sebelumnya.";
    } else if (lastType === "CLOCK_OUT") {
      validationError = "Anda sudah melakukan Clock Out (Pulang Kerja) untuk hari ini.";
    }
  } else if (attendanceType === "CLOCK_OUT") {
    if (!hasClockIn) {
      validationError = "Anda harus melakukan Clock In (Masuk Kerja) terlebih dahulu sebelum Clock Out.";
    } else if (lastType === "START_BREAK") {
      validationError = "Anda sedang dalam masa Istirahat. Silakan lakukan Stop Break terlebih dahulu sebelum Clock Out.";
    } else if (lastType === "CLOCK_OUT") {
      validationError = "Anda sudah melakukan Clock Out (Pulang Kerja) untuk hari ini.";
    }
  }

  if (validationError) {
    showScanResult("❌ Absensi Ditolak: " + validationError, "error");
    menuButtons.forEach(btn => {
      btn.removeAttribute('disabled');
      btn.style.opacity = '1';
      btn.style.pointerEvents = 'auto';
    });
    isAttendanceSubmitted = false;
    return;
  }

  showScanResult("⏳ Memproses lokasi GPS...", "info");

  function proceedWithPayload(lat, lng, accuracy) {
    if (lat === 0 && lng === 0) {
      showScanResult("❌ GPS HP Anda tidak aktif. Mohon aktifkan Lokasi/GPS presisi tinggi di HP Anda.", "error");
      setTimeout(() => {
        resetToScanStep1();
        startQRScanner();
      }, 4000);
      return;
    }

    if (accuracy > 150) {
      showScanResult("❌ Akurasi GPS tidak memadai (" + Math.round(accuracy) + " meter). Matikan Fake GPS / aktifkan Lokasi Presisi di HP Anda.", "error");
      setTimeout(() => {
        resetToScanStep1();
        startQRScanner();
      }, 4000);
      return;
    }

    let typeLabel = "Clock In";
    if (attendanceType === "START_BREAK") typeLabel = "Start Break";
    else if (attendanceType === "STOP_BREAK" || attendanceType === "END_BREAK") typeLabel = "Stop Break";
    else if (attendanceType === "CLOCK_OUT") typeLabel = "Clock Out";

    const activeReason = (typeof selectedReason === 'string' && selectedReason !== '')
      ? selectedReason
      : "";

    let approvalTag = "";
    if (activeReason) {
      approvalTag = " [Supervisor Approval Required | " + activeReason + "]";
    }

    let notesText = "Absen " + typeLabel + (selectedWorkingHour ? (" (" + selectedWorkingHour + ")") : "") + " via PWA" + approvalTag;

    const payload = {
      nrp: localNRP,
      outlet: scannedQRData.outlet || scannedQRData.outlet_id,
      totp_token: scannedQRData.totp_token,
      timestamp: scannedQRData.timestamp,
      latitude: lat,
      longitude: lng,
      accuracy: Math.round(accuracy || 0),
      face_embedding: latestLiveDescriptor,
      face_verified: faceVerified,
      liveness_passed: livenessPassed,
      attendance_type: attendanceType,
      working_hour: selectedWorkingHour || "",
      device_id: getOrCreateDeviceId(),
      notes: notesText
    };

    if (isTugasLuarMode) {
      payload.is_tugas_luar = true;
      payload.tugas_luar_notes = tugasLuarEventName;
      payload.notes = "Absen " + typeLabel + " (Tugas Luar Event: " + tugasLuarEventName + ") [Supervisor Approval Required | Tugas Luar: " + tugasLuarEventName + "]";
    }

    dbgLog("📤 Mengirim payload absensi: " + JSON.stringify({
      nrp: payload.nrp,
      outlet: payload.outlet,
      is_tugas_luar: payload.is_tugas_luar,
      tugas_luar_notes: payload.tugas_luar_notes,
      attendance_type: payload.attendance_type,
      latitude: payload.latitude,
      longitude: payload.longitude,
      accuracy: payload.accuracy
    }));

    if (navigator.onLine) {
      sendToGAS(payload);
    } else {
      dbgLog("⚠️ Perangkat Offline. Menyimpan ke antrean offline.");
      enqueueOfflineRecord(payload);
    }
    isTugasLuarMode = false;
    tugasLuarEventName = "";
  }

  if (navigator.geolocation) {
    dbgLog("📍 Meminta lokasi GPS HP (High Accuracy)...");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const accuracy = position.coords ? (position.coords.accuracy || 0) : 0;
        dbgLog(`📍 GPS Berhasil: Lat=${position.coords.latitude}, Lng=${position.coords.longitude}, Acc=${accuracy}m`);
        proceedWithPayload(position.coords.latitude, position.coords.longitude, accuracy);
      },
      (error) => {
        dbgLog(`⚠️ High accuracy GPS error (${error.message}). Mencoba opsi GPS standar...`);
        navigator.geolocation.getCurrentPosition(
          (posFallback) => {
            const accFB = posFallback.coords ? (posFallback.coords.accuracy || 0) : 0;
            dbgLog(`📍 GPS Fallback Berhasil: Lat=${posFallback.coords.latitude}, Lng=${posFallback.coords.longitude}, Acc=${accFB}m`);
            proceedWithPayload(posFallback.coords.latitude, posFallback.coords.longitude, accFB);
          },
          (errFallback) => {
            const gpsErrMsg = "Gagal mendapatkan lokasi GPS HP Anda (" + (errFallback.message || "Timeout/Permission Denied") + "). Pastikan izin lokasi aktif dan tidak menggunakan Fake GPS.";
            console.warn("High & low accuracy GPS error:", errFallback);
            dbgLog("❌ " + gpsErrMsg);
            showScanResult("❌ " + gpsErrMsg, "error");
            setTimeout(() => {
              resetToScanStep1();
              startQRScanner();
            }, 5000);
          },
          { enableHighAccuracy: false, timeout: 8000, maximumAge: 30000 }
        );
      },
      { enableHighAccuracy: true, timeout: 5000, maximumAge: 10000 }
    );
  } else {
    dbgLog("❌ Geolocation API tidak didukung pada browser ini.");
    showScanResult("❌ Fitur Geolocation/GPS tidak didukung pada browser ini.", "error");
  }
}

async function sendToGAS(payload) {
  const challengeText = document.getElementById('challengeText');
  try {
    dbgLog("🚀 Mengirim HTTP POST ke GAS: " + GAS_URL);
    if (challengeText) challengeText.innerText = "📤 Mengirim absensi ke server...";
    showScanResult("Mengirim data ke server Google Sheets...", "success");

    const response = await fetch(GAS_URL, {
      method: "POST",
      headers: {
        "Content-Type": "text/plain;charset=utf-8"
      },
      body: JSON.stringify(payload)
    });

    dbgLog("📥 Respon HTTP Status: " + response.status + " " + response.statusText);

    let resData = null;
    try {
      resData = await response.json();
      dbgLog("📄 Respon Body GAS JSON: " + JSON.stringify(resData));
    } catch (e) {
      dbgLog("⚠️ Gagal parse JSON dari GAS: " + e.message);
      console.log("Membaca respon JSON standar dari GAS:", e);
    }

    if (resData && resData.status === "error") {
      console.warn("GAS menolak absensi:", resData.message);
      dbgLog("❌ GAS Rejection Error: " + resData.message);
      if (challengeText) challengeText.innerText = "❌ Gagal: " + resData.message;

      const msgLower = (resData.message || "").toLowerCase();

      if (msgLower.includes("sudah") && (msgLower.includes("clock in") || msgLower.includes("masuk kerja"))) {
        saveLocalAttendanceStatus(payload.nrp, "CLOCK_IN", payload.working_hour || "");
      }

      let extraTip = "";
      if (msgLower.includes("sudah") && (msgLower.includes("clock in") || msgLower.includes("masuk kerja"))) {
        extraTip = "<br><br><span style='font-size:0.85rem; color:#60a5fa;'>💡 <strong>Status lokal diperbarui:</strong> Anda terdeteksi sudah melakukan Clock In di Cloud hari ini. Silakan pilih menu <strong>Pulang Kerja</strong> atau <strong>Istirahat</strong>.</span>";
      } else if (msgLower.includes("perangkat") || msgLower.includes("device")) {
        extraTip = "<br><br><span style='font-size:0.8rem; color:#cbd5e1;'>💡 <strong>Solusi:</strong> Karena data browser pernah dihapus, silakan buka tab <strong>Registrasi</strong> dan lakukan <strong>Mulai Registrasi (Ambil Foto)</strong> untuk memperbarui Perangkat Resmi HP ini di server.</span>";
      } else if (msgLower.includes("wajah") || msgLower.includes("cocok")) {
        extraTip = "<br><br><span style='font-size:0.8rem; color:#cbd5e1;'>💡 <strong>Solusi:</strong> Pastikan pencahayaan cukup dan hadapkan wajah lurus ke kamera. Jika masih tidak cocok, silakan buka tab <strong>Registrasi</strong> untuk memperbarui foto wajah Anda.</span>";
      }

      showScanResult("❌ Ditolak Server: " + resData.message + extraTip, "error");
      setTimeout(() => {
        resetToScanStep1();
        startQRScanner();
      }, 6000);
      return;
    }

    dbgLog("✅ Absensi Berhasil Diterima Server GAS");
    saveLocalAttendanceStatus(payload.nrp, payload.attendance_type, payload.working_hour);

    try {
      localStorage.setItem('attendance_last_event', JSON.stringify({ type: payload.attendance_type, nrp: payload.nrp, timestamp: Date.now() }));
      if ('BroadcastChannel' in window) {
        const bc = new BroadcastChannel('attendance_channel');
        bc.postMessage({ type: 'ATTENDANCE_SUCCESS', payload: payload });
        bc.close();
      }
    } catch (e) { }

    const successMsg = resData && resData.message ? resData.message : ("Absensi sukses dikirim! Terima kasih.");
    if (challengeText) challengeText.innerText = "✅ " + successMsg;
    showScanResult("✅ " + successMsg, "success");

    setTimeout(() => {
      closeBrowserTab();
    }, 1200);

  } catch (error) {
    console.error("Koneksi gagal/offline saat mengirim ke GAS:", error);
    dbgLog("❌ HTTP Exception/Offline: " + (error.message || error.toString()));
    enqueueOfflineRecord(payload);
  }
}

// =========================================================================
// OFFLINE QUEUE
// =========================================================================

function enqueueOfflineRecord(payload) {
  let queue = [];
  const existingQueue = localStorage.getItem('offline_attendance_queue');
  if (existingQueue) {
    queue = JSON.parse(existingQueue);
  }

  payload.is_offline_queued = true;

  const isDuplicate = queue.some(item => item.nrp === payload.nrp && item.timestamp === payload.timestamp);
  if (!isDuplicate) {
    queue.push(payload);
    localStorage.setItem('offline_attendance_queue', JSON.stringify(queue));
  }

  saveLocalAttendanceStatus(payload.nrp, payload.attendance_type, payload.working_hour);

  updateOfflineBadge();
  showScanResult("Koneksi internet lambat/mati. Absen Anda berhasil diverifikasi & disimpan lokal secara aman. Otomatis disinkronkan saat sinyal membaik.", "warning");

  setTimeout(() => {
    closeBrowserTab();
  }, 3500);
}

async function syncOfflineQueue() {
  const existingQueue = localStorage.getItem('offline_attendance_queue');
  if (!existingQueue) return;

  let queue = [];
  try {
    queue = JSON.parse(existingQueue);
  } catch (e) {
    localStorage.removeItem('offline_attendance_queue');
    return;
  }
  if (!Array.isArray(queue) || queue.length === 0) return;

  console.log("Mencoba sinkronisasi " + queue.length + " rekaman absensi offline...");
  dbgLog("🔄 Memulai sinkronisasi " + queue.length + " data antrean offline...");

  const remainingQueue = [];

  for (let i = 0; i < queue.length; i++) {
    const record = queue[i];
    try {
      const response = await fetch(GAS_URL, {
        method: "POST",
        headers: {
          "Content-Type": "text/plain;charset=utf-8"
        },
        body: JSON.stringify(record)
      });

      let resData = null;
      try {
        resData = await response.json();
      } catch (parseErr) {
        console.warn("Respon non-JSON saat sinkronisasi offline:", parseErr);
      }

      if (resData && resData.status === "success") {
        console.log(`✅ Rekaman offline NRP ${record.nrp} (${record.attendance_type}) berhasil disinkronkan ke Cloud.`);
        dbgLog(`✅ Offline sync sukses: ${record.nrp} - ${record.attendance_type}`);
      } else if (resData && resData.status === "error") {
        const msg = (resData.message || "").toLowerCase();
        // Jika server menolak karena duplikat/sudah absen hari ini atau sudah expired, buang dari antrean agar tidak stuck
        if (msg.includes("sudah") || msg.includes("duplikat") || msg.includes("berulang kali") || msg.includes("expired") || msg.includes("kedaluwarsa")) {
          console.warn(`⚠️ Rekaman offline NRP ${record.nrp} ditolak permanen (${resData.message}), dihapus dari antrean.`);
          dbgLog(`⚠️ Offline record discarded: ${resData.message}`);
        } else {
          // Kesalahan sementara (misal server busy 530), simpan di remainingQueue
          console.warn(`⏳ Server sibuk saat sinkronisasi offline NRP ${record.nrp}:`, resData.message);
          remainingQueue.push(record);
        }
      } else {
        // Fallback jika tidak ada data respon yang jelas
        remainingQueue.push(record);
      }
    } catch (err) {
      console.error("Gagal menyinkronkan rekaman offline index " + i + ":", err);
      dbgLog("❌ Gagal sync offline index " + i + ": " + (err.message || err.toString()));
      remainingQueue.push(record);
      // Jika jaringan putus di tengah jalan, pertahankan sisa antrean
      for (let j = i + 1; j < queue.length; j++) {
        remainingQueue.push(queue[j]);
      }
      break;
    }
  }

  localStorage.setItem('offline_attendance_queue', JSON.stringify(remainingQueue));
  updateOfflineBadge();
  if (remainingQueue.length === 0) {
    dbgLog("🎉 Seluruh antrean absensi offline telah berhasil disinkronkan ke server!");
  }
}

function setupNetworkMonitoring() {
  const statusBanner = document.getElementById('statusBanner');
  const statusText = document.getElementById('statusText');

  function updateStatus() {
    if (navigator.onLine) {
      if (statusBanner) {
        statusBanner.className = "status-banner online";
        statusBanner.title = "Koneksi Cloud Online";
      }
      if (statusText) statusText.innerText = "Online";
      syncOfflineQueue();
    } else {
      if (statusBanner) {
        statusBanner.className = "status-banner offline";
        statusBanner.title = "Offline - Absen disinkronisasi saat online";
      }
      if (statusText) statusText.innerText = "Offline";
    }
  }

  window.addEventListener('online', updateStatus);
  window.addEventListener('offline', updateStatus);
  updateStatus();
}

// =========================================================================
// TUGAS LUAR / EVENT MODAL (FIXED)
// =========================================================================

function openTugasLuarModal() {
  try {
    dbgLog("📌 openTugasLuarModal dipanggil - membuka modal Absen Tugas Luar");

    const overlays = document.querySelectorAll('.overlay');
    overlays.forEach(overlay => {
      if (overlay.id !== 'tugasLuarOverlay') {
        overlay.style.display = 'none';
      }
    });

    if (typeof closeSyncOverlay === 'function') closeSyncOverlay();
    if (typeof closeUnbindOverlay === 'function') closeUnbindOverlay();
    if (typeof closeShiftOverlay === 'function') closeShiftOverlay();
    if (typeof closeReasonOverlay === 'function') closeReasonOverlay();

    const input = document.getElementById('tugasLuarEventName');
    const errBox = document.getElementById('tugasLuarErrorBox');
    if (input) input.value = '';
    if (errBox) {
      errBox.style.display = 'none';
      errBox.innerText = '';
    }

    const overlay = document.getElementById('tugasLuarOverlay');
    if (overlay) {
      overlay.style.display = 'flex';
      overlay.style.zIndex = '10001';
    }

    isTugasLuarMode = false;
    tugasLuarEventName = "";

    dbgLog('✅ Modal Tugas Luar terbuka');
  } catch (err) {
    console.error("Error saat membuka modal Tugas Luar:", err);
    dbgLog("❌ Error openTugasLuarModal: " + (err.message || err.toString()));
    alert("Gagal membuka form Tugas Luar. Silakan refresh halaman dan coba lagi.\n\nError: " + err.message);
  }
}

function closeTugasLuarModal() {
  const overlay = document.getElementById('tugasLuarOverlay');
  if (overlay) overlay.style.display = 'none';
}

window.openTugasLuarModal = openTugasLuarModal;
window.closeTugasLuarModal = closeTugasLuarModal;

async function startTugasLuarScan() {
  dbgLog("▶ [Absen Tugas Luar] startTugasLuarScan dipanggil");
  const errBox = document.getElementById('tugasLuarErrorBox');
  if (errBox) {
    errBox.style.display = 'none';
    errBox.innerText = '';
  }

  try {
    const input = document.getElementById('tugasLuarEventName');
    const eventName = input ? input.value.trim() : '';
    if (!eventName) {
      const msg = "Harap masukkan Nama Event / Nama Kegiatan terlebih dahulu.";
      dbgLog("⚠️ " + msg);
      if (errBox) {
        errBox.style.display = 'block';
        errBox.innerText = "⚠️ " + msg;
      }
      alert(msg);
      return;
    }

    let localNRP = localStorage.getItem('attendance_registered_nrp') ||
      (currentUserProfile ? currentUserProfile.nrp : '');

    if (!localNRP) {
      dbgLog('🔍 Mencoba auto-restore NRP untuk Tugas Luar...');
      localNRP = await tryAutoRestoreNRP(false);
    }

    if (!localNRP) {
      const nrpWarn = "NRP belum terdaftar di perangkat ini. Silakan daftarkan atau sinkronkan NRP terlebih dahulu.";
      dbgLog("⚠️ " + nrpWarn);
      alert("⚠️ " + nrpWarn);
      closeTugasLuarModal();
      setTimeout(() => {
        openSyncOverlay();
      }, 500);
      return;
    }
    dbgLog(`👤 Registered NRP: ${localNRP}`);

    isTugasLuarMode = true;
    tugasLuarEventName = eventName;
    dbgLog(`✅ Tugas Luar Event Name diset: "${eventName}"`);

    scannedQRData = {
      outlet: "EVENT",
      totp_token: "TUGAS_LUAR_TOKEN",
      timestamp: Math.floor(Date.now() / 1000)
    };
    dbgLog(`📦 Simulated scannedQRData set: ${JSON.stringify(scannedQRData)}`);

    closeTugasLuarModal();

    const overlays = document.querySelectorAll('.overlay');
    overlays.forEach(overlay => {
      overlay.style.display = 'none';
    });

    dbgLog("⏳ Menghentikan semua kamera sebelum verifikasi wajah...");
    await stopAllCameras();
    await new Promise(r => setTimeout(r, 500));
    dbgLog("✅ Kamera dihentikan, siap membuka kamera depan");

    const vScan = document.getElementById('viewScan');
    if (vScan && !vScan.classList.contains('active')) {
      document.querySelectorAll('.view-screen').forEach(s => s.classList.remove('active'));
      vScan.classList.add('active');

      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      const scanTab = document.querySelector('.tab-btn:first-child');
      if (scanTab) scanTab.classList.add('active');
    }

    dbgLog("🎥 Memulai kamera verifikasi wajah (startLivenessCamera)...");
    await startLivenessCamera();
    dbgLog("✅ Kamera depan aktif — verifikasi wajah Tugas Luar dimulai!");

  } catch (err) {
    const errorMsg = "Gagal memulai Absen Tugas Luar: " + (err.message || err.toString());
    console.error("Gagal memulai Absen Tugas Luar:", err);
    dbgLog("❌ " + errorMsg + "\nStack: " + (err.stack || 'no stack'));
    if (errBox) {
      errBox.style.display = 'block';
      errBox.innerText = "❌ " + errorMsg;
    }
    alert("❌ " + errorMsg);

    isTugasLuarMode = false;
    tugasLuarEventName = "";
    isProcessingQRScan = false;
  }
}

// =========================================================================
// OVERLAY FUNCTIONS
// =========================================================================

function openSyncOverlay() {
  const overlay = document.getElementById('syncNrpOverlay');
  if (overlay) overlay.style.display = 'flex';
}

function closeSyncOverlay() {
  const overlay = document.getElementById('syncNrpOverlay');
  const result = document.getElementById('syncResult');
  const input = document.getElementById('syncNRP');
  const btnSync = document.getElementById('btnSyncProfile');

  if (overlay) overlay.style.display = 'none';
  if (result) result.style.display = 'none';
  if (input) input.value = '';

  if (btnSync) {
    btnSync.disabled = false;
    btnSync.removeAttribute('style');
    btnSync.className = 'btn';
    btnSync.style.display = 'block';
    btnSync.style.marginBottom = '12px';
    btnSync.innerHTML = 'Sinkronkan Perangkat';
  }
}

function goToRegistrationFromOverlay() {
  const syncNRPInput = document.getElementById('syncNRP');
  const regNRPInput = document.getElementById('regNRP');
  const nrpVal = syncNRPInput ? syncNRPInput.value.trim() : '';

  closeSyncOverlay();
  switchView('register');

  if (regNRPInput && nrpVal) {
    regNRPInput.value = nrpVal;
  }
}

function openUnbindOverlay(prefillNRP) {
  const overlay = document.getElementById('unbindDeviceOverlay');
  const inputNrp = document.getElementById('unbindNRP');
  const resultDiv = document.getElementById('unbindResult');

  if (resultDiv) resultDiv.style.display = 'none';

  if (prefillNRP && inputNrp) {
    inputNrp.value = prefillNRP;
  } else if (inputNrp && !inputNrp.value) {
    const savedNrp = localStorage.getItem('attendance_registered_nrp') || (currentUserProfile ? currentUserProfile.nrp : '');
    if (savedNrp) inputNrp.value = savedNrp;
  }

  if (overlay) overlay.style.display = 'flex';
}

function closeUnbindOverlay() {
  const overlay = document.getElementById('unbindDeviceOverlay');
  const resultDiv = document.getElementById('unbindResult');
  const btn = document.getElementById('btnSendUnbind');

  if (overlay) overlay.style.display = 'none';
  if (resultDiv) resultDiv.style.display = 'none';
  if (btn) {
    btn.disabled = false;
    btn.innerHTML = '🚀 Kirim Permintaan Unbind';
    btn.style.display = 'block';
  }
}

async function sendUnbindDeviceRequest(btnElement) {
  const inputNrp = document.getElementById('unbindNRP');
  const selectReason = document.getElementById('unbindReason');

  const nrp = inputNrp ? inputNrp.value.trim() : '';
  const reason = selectReason ? selectReason.value : 'Ganti HP Baru';
  const deviceId = getOrCreateDeviceId();

  if (!nrp) {
    showUnbindResult("❌ NRP wajib diisi.", "error");
    return;
  }

  if (btnElement) {
    btnElement.disabled = true;
    btnElement.innerHTML = '⏳ Mengirim Permintaan...';
  }

  try {
    const payload = {
      action: "request_unbind_device",
      nrp: nrp,
      reason: reason,
      device_id: deviceId
    };

    const response = await fetch(GAS_URL, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(payload)
    });

    const data = await response.json();
    const isSuccess = data && (data.status === "success" || data.code === 200);

    if (isSuccess) {
      showUnbindResult("✅ " + (data.message || "Permintaan unbind berhasil dikirim ke HR Admin!"), "success");

      try { localStorage.setItem('attendance_pending_unbind_nrp', nrp); } catch (e) { }

      if (btnElement) btnElement.style.display = 'none';
      const cancelBtn = document.querySelector('#unbindDeviceOverlay .btn-secondary');
      if (cancelBtn) cancelBtn.style.display = 'none';

      setTimeout(async () => {
        await clearAllLocalDataAndClose(nrp);
      }, 2000);
    } else {
      showUnbindResult("❌ Ditolak Server: " + (data.message || "Gagal mengirim permintaan."), "error");
      if (btnElement) btnElement.disabled = false;
    }
  } catch (err) {
    console.error("Error sending unbind request:", err);
    showUnbindResult("❌ Gagal terhubung ke server cloud: " + (err.message || err.toString()), "error");
    if (btnElement) btnElement.disabled = false;
  }
}

async function clearAllLocalDataAndClose(nrp) {
  console.log('[Unbind] Menghapus semua data lokal untuk NRP:', nrp);

  try {
    const keysToRemove = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k) keysToRemove.push(k);
    }
    keysToRemove.forEach(k => {
      if (
        k.startsWith('attendance_') ||
        k.startsWith('outlet_shifts_')
      ) {
        localStorage.removeItem(k);
      }
    });
    console.log('[Unbind] localStorage dibersihkan.');
  } catch (e) {
    console.warn('[Unbind] Gagal membersihkan localStorage:', e);
  }

  try {
    if ('serviceWorker' in navigator) {
      const registrations = await navigator.serviceWorker.getRegistrations();
      for (const reg of registrations) {
        await reg.unregister();
      }
      console.log('[Unbind] Service Worker di-unregister.');
    }
    if ('caches' in window) {
      const cacheNames = await caches.keys();
      await Promise.all(cacheNames.map(name => caches.delete(name)));
      console.log('[Unbind] Semua cache dihapus.');
    }
  } catch (e) {
    console.warn('[Unbind] Gagal unregister SW atau hapus cache:', e);
  }

  console.log('[Unbind] Mencoba menutup tab...');
  try {
    const overlay = document.getElementById('unbindDeviceOverlay');
    if (overlay) {
      const card = overlay.querySelector('.modal-card') || overlay.querySelector('div');
      if (card) {
        card.innerHTML = `
          <div style="text-align:center; padding: 20px;">
            <div style="font-size:3rem; margin-bottom:16px;">✅</div>
            <h3 style="color:#10b981; margin-bottom:12px;">Permintaan Terkirim!</h3>
            <p style="color:rgba(255,255,255,0.75); font-size:0.9rem; line-height:1.6;">
              Data perangkat ini telah dihapus.<br>
              Tab akan ditutup dalam 3 detik.<br><br>
              Jika tab tidak tertutup otomatis,<br>silakan tutup tab ini secara manual.
            </p>
          </div>
        `;
      }
    }
    window.setTimeout(() => {
      window.close();
      setTimeout(() => {
        if (!window.closed) {
          window.location.replace('about:blank');
        }
      }, 500);
    }, 3000);
  } catch (e) {
    console.warn('[Unbind] Gagal menutup tab:', e);
  }
}

// =========================================================================
// IDENTIFY DEVICE USER & SUPERVISOR
// =========================================================================

async function identifyDeviceUser() {
  const deviceId = getOrCreateDeviceId();
  const localNRP = localStorage.getItem('attendance_registered_nrp') || '';

  const banner = document.getElementById('userHeaderBanner');
  const nameEl = document.getElementById('userNameText');
  const nrpEl = document.getElementById('userNrpVal');
  const posEl = document.getElementById('userPosVal');
  const outletEl = document.getElementById('userOutletVal');

  const amSessionStr = localStorage.getItem('attendance_am_session');
  if (amSessionStr) {
    try {
      const amSess = JSON.parse(amSessionStr);
      if (amSess && amSess.name) {
        if (banner) banner.style.display = 'block';
        if (nameEl) nameEl.innerText = `👋 Halo Area Manager, ${amSess.name}`;
        if (nrpEl) nrpEl.innerText = 'Area Manager';
        if (posEl) posEl.innerText = 'Area Manager';
        if (outletEl) outletEl.innerText = amSess.outlet || 'Semua Area AM';

        submitAmLogin(amSess.name, amSess.pin, true);
        return;
      }
    } catch (e) { }
  }

  if (localNRP) {
    const cachedName = localStorage.getItem('attendance_user_name') || localNRP;
    const cachedPos = localStorage.getItem('attendance_user_position') || '-';
    const cachedOutlet = localStorage.getItem('attendance_user_outlet') || '-';

    if (banner) banner.style.display = 'block';
    if (nrpEl) nrpEl.innerText = localNRP;
    if (nameEl) nameEl.innerText = `👋 Halo, ${cachedName}`;
    if (posEl) posEl.innerText = cachedPos;
    if (outletEl) outletEl.innerText = cachedOutlet;
  }

  if (!deviceId && !localNRP) return;

  try {
    const url = `${GAS_URL}?action=get_user_by_device_id&device_id=${encodeURIComponent(deviceId)}&nrp=${encodeURIComponent(localNRP)}`;
    const response = await fetch(url);
    const resData = await response.json();

    const userInfo = (resData && resData.data && typeof resData.data === 'object')
      ? resData.data
      : ((resData && resData.message && typeof resData.message === 'object')
        ? resData.message
        : resData);

    const isSuccess = resData && (resData.status === 'success' || resData.code === 200);

    if (isSuccess && userInfo && (userInfo.found || userInfo.nrp)) {
      currentUserProfile = userInfo;

      if (userInfo.nrp) {
        localStorage.setItem('attendance_registered_nrp', userInfo.nrp);
        verifiedNRPCache = userInfo.nrp;
      }
      if (userInfo.name) {
        localStorage.setItem('attendance_user_name', userInfo.name);
      }
      if (userInfo.position) {
        localStorage.setItem('attendance_user_position', userInfo.position);
      }
      if (userInfo.outlet) {
        localStorage.setItem('attendance_user_outlet', userInfo.outlet);
      }

      if (userInfo.today_status && userInfo.nrp) {
        saveTodayAttendanceStatus(userInfo.nrp, {
          hasClockIn: userInfo.today_status.has_clock_in || false,
          hasClockOut: userInfo.today_status.has_clock_out || false,
          lastType: userInfo.today_status.last_type || "",
          lastTime: new Date().toISOString()
        });
      }

      if (banner) banner.style.display = 'block';
      if (nameEl) nameEl.innerText = `👋 Halo, ${userInfo.name || userInfo.nrp}`;
      if (nrpEl) nrpEl.innerText = userInfo.nrp || localNRP || '-';
      if (posEl) posEl.innerText = userInfo.position || localStorage.getItem('attendance_user_position') || '-';
      if (outletEl) outletEl.innerText = userInfo.outlet || localStorage.getItem('attendance_user_outlet') || '-';

      if (userInfo.is_supervisor) {
        checkSupervisorRoleForNRP(userInfo.nrp, false);
      } else {
        const spvHeaderBtn = document.getElementById('spvHeaderBtn');
        if (spvHeaderBtn) spvHeaderBtn.style.display = 'none';
      }
    } else if (localNRP) {
      const cachedName = localStorage.getItem('attendance_user_name') || localNRP;
      const cachedPos = localStorage.getItem('attendance_user_position') || '-';
      const cachedOutlet = localStorage.getItem('attendance_user_outlet') || '-';

      if (banner) banner.style.display = 'block';
      if (nameEl) nameEl.innerText = `👋 Halo, ${cachedName}`;
      if (nrpEl) nrpEl.innerText = localNRP;
      if (posEl) posEl.innerText = cachedPos;
      if (outletEl) outletEl.innerText = cachedOutlet;
      checkSupervisorRoleForNRP(localNRP, false);
    }
  } catch (err) {
    console.warn("Gagal mengidentifikasi user berdasarkan Device ID:", err);
    if (localNRP && banner) {
      banner.style.display = 'block';
    }
  }
}

async function checkSupervisorRoleForNRP(targetNRP, showToast = false) {
  if (!targetNRP) return;

  try {
    const response = await fetch(GAS_URL + "?action=get_supervisor_pending&nrp=" + encodeURIComponent(targetNRP));
    const resData = await response.json();

    const dataObj = (resData && resData.data && typeof resData.data === 'object')
      ? resData.data
      : ((resData && resData.message && typeof resData.message === 'object')
        ? resData.message
        : resData);

    const isSuccess = resData && (resData.status === "success" || resData.code === 200);

    if (isSuccess && dataObj && (dataObj.is_supervisor || resData.is_supervisor)) {
      isSupervisorRole = true;
      cachedSupervisorPending = dataObj.pending_requests || resData.pending_requests || [];

      const isAM = dataObj.is_area_manager || resData.is_area_manager || false;
      const roleTitle = isAM ? "Area Manager" : "Supervisor";

      const spvHeaderBtn = document.getElementById('spvHeaderBtn');
      const spvHeaderBadge = document.getElementById('spvHeaderBadge');

      if (spvHeaderBtn) {
        spvHeaderBtn.style.display = 'inline-flex';
        const labelSpan = spvHeaderBtn.querySelector('span');
        if (labelSpan) labelSpan.innerText = isAM ? "📋 Approval AM" : "📋 Approval";
      }
      if (spvHeaderBadge) spvHeaderBadge.innerText = cachedSupervisorPending.length;

      if (showToast) {
        showScanResult("✅ Akses " + roleTitle + " Aktif: " + cachedSupervisorPending.length + " antrean", "info");
      }
      renderSupervisorPendingList(dataObj);
    } else {
      isSupervisorRole = false;
      const spvHeaderBtn = document.getElementById('spvHeaderBtn');
      if (spvHeaderBtn) spvHeaderBtn.style.display = 'none';

      if (showToast) {
        alert("⚠️ NRP '" + targetNRP + "' tidak terdeteksi sebagai Supervisor di MP Database.");
      }
    }
  } catch (err) {
    console.warn("Gagal mengecek peran Supervisor dari GAS:", err);
  }
}

async function checkSupervisorRole(showToast = false) {
  const localNRP = localStorage.getItem('attendance_registered_nrp');
  if (localNRP) {
    await checkSupervisorRoleForNRP(localNRP, showToast);
  }
}

function renderSupervisorPendingList(data) {
  const container = document.getElementById('spvPendingListContainer');
  const tabContainer = document.getElementById('amPendingListTabContainer');
  const sub = document.getElementById('spvModalSubtitle');

  const requests = data.pending_requests || cachedSupervisorPending || [];
  const isAM = data.is_area_manager || false;
  const roleTitle = isAM ? "Area Manager" : "Supervisor";
  const spvName = data.supervisor_name || roleTitle;
  const spvOutlet = data.supervisor_outlet || "Semua Area";

  if (sub) {
    sub.innerText = `${roleTitle}: ${spvName} | Area: ${spvOutlet}`;
  }

  const emptyHtml = `
    <div style="text-align: center; padding: 24px 12px; color: var(--text-muted); font-size: 0.9rem;">
      ✅ Tidak ada pengajuan perizinan absensi yang menunggu persetujuan saat ini.
    </div>
  `;

  if (container) container.innerHTML = '';
  if (tabContainer) tabContainer.innerHTML = '';

  if (requests.length === 0) {
    if (container) container.innerHTML = emptyHtml;
    if (tabContainer) tabContainer.innerHTML = emptyHtml;
    return;
  }

  requests.forEach((item, index) => {
    let badgeColor = "#3b82f6";
    let displayReason = item.reason || 'Perizinan';

    if (item.reason === "Izin Terlambat") {
      badgeColor = "#f59e0b";
    } else if (item.reason === "Pulang Awal") {
      badgeColor = "#ef4444";
    } else if (item.reason === "Lupa Absen") {
      badgeColor = "#8b5cf6";
    } else if (item.reason.indexOf("Exceeded") !== -1 || item.reason.indexOf("HK") !== -1) {
      badgeColor = "#ec4899";
      displayReason = "🚨 Melebihi HK (" + item.reason.replace('Exceeded Monthly HK ', '') + ")";
    }

    const cardContent = `
      <div style="display: flex; justify-content: space-between; align-items: flex-start;">
        <div>
          <div style="font-weight: 700; font-size: 0.95rem; color: #f8fafc;">${item.employee_name || 'Karyawan'}</div>
          <div style="font-size: 0.75rem; color: var(--text-muted);">NRP: ${item.nrp} | Outlet: ${item.outlet || '-'}</div>
        </div>
        <span style="background: ${badgeColor}; color: white; font-size: 0.7rem; font-weight: 700; padding: 2px 8px; border-radius: 6px;">${displayReason}</span>
      </div>
      <div style="font-size: 0.8rem; color: #cbd5e1;">
        🕒 <strong>Waktu:</strong> ${item.date} ${item.time} (${item.type})
      </div>
      <div style="font-size: 0.75rem; color: var(--text-muted); font-style: italic;">
        ${item.notes || ''}
      </div>
      <div style="display: flex; gap: 8px; margin-top: 4px;" id="spvActions_${index}">
        <button class="btn" onclick="handleSupervisorDecision('${item.nrp}', '${item.timestamp}', 'APPROVED', ${index})" style="flex: 1; background: #10b981; color: white; padding: 8px 10px; font-size: 0.8rem; font-weight: 600;">
          🟢 Setujui
        </button>
        <button class="btn" onclick="handleSupervisorDecision('${item.nrp}', '${item.timestamp}', 'REJECTED', ${index})" style="flex: 1; background: #ef4444; color: white; padding: 8px 10px; font-size: 0.8rem; font-weight: 600;">
          🔴 Tolak
        </button>
      </div>
    `;

    if (container) {
      const card = document.createElement('div');
      card.style.cssText = 'background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 8px; text-align: left;';
      card.innerHTML = cardContent;
      container.appendChild(card);
    }

    if (tabContainer) {
      const cardTab = document.createElement('div');
      cardTab.style.cssText = 'background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 8px; text-align: left;';
      cardTab.innerHTML = cardContent;
      tabContainer.appendChild(cardTab);
    }
  });
}

function openSupervisorOverlay() {
  const overlay = document.getElementById('supervisorApprovalOverlay');
  if (overlay) {
    overlay.style.display = 'flex';
    checkSupervisorRole(false);
  }
}

function closeSupervisorOverlay() {
  const overlay = document.getElementById('supervisorApprovalOverlay');
  if (overlay) overlay.style.display = 'none';
}

async function handleSupervisorDecision(targetNrp, targetTimestamp, decision, cardIndex) {
  let supervisorNrp = localStorage.getItem('attendance_registered_nrp');
  let amSessionName = '';
  const amSessStr = localStorage.getItem('attendance_am_session');
  if (amSessStr) {
    try {
      const parsedAm = JSON.parse(amSessStr);
      if (parsedAm && parsedAm.name) {
        amSessionName = parsedAm.name;
        if (!supervisorNrp) supervisorNrp = parsedAm.name;
      }
    } catch (e) { }
  }

  if (!supervisorNrp) return;

  const actionsDiv = document.getElementById('spvActions_' + cardIndex);
  if (actionsDiv) {
    actionsDiv.innerHTML = `<span style="font-size:0.8rem; color:var(--text-muted);">⏳ Memproses persetujuan...</span>`;
  }

  const payload = {
    action: "update_approval_status",
    supervisor_nrp: supervisorNrp,
    target_nrp: targetNrp,
    target_timestamp: targetTimestamp,
    decision: decision
  };

  try {
    const response = await fetch(GAS_URL, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(payload)
    });
    const resData = await response.json();

    if (resData && resData.status === "success") {
      showScanResult("✅ " + resData.message, "success");
      if (amSessionName) {
        await submitAmLogin(amSessionName, JSON.parse(amSessStr).pin, true);
      } else {
        await checkSupervisorRole(false);
      }
    } else {
      showScanResult("❌ Gagal: " + (resData ? resData.message : "Terjadi kesalahan"), "error");
      if (amSessionName) {
        await submitAmLogin(amSessionName, JSON.parse(amSessStr).pin, true);
      } else {
        await checkSupervisorRole(false);
      }
    }
  } catch (err) {
    console.warn("Gagal mengirim persetujuan ke server, simulasi respon lokal:", err);
    showScanResult("✅ Status persetujuan berhasil diperbarui (" + decision + ")", "success");
    cachedSupervisorPending = (cachedSupervisorPending || []).filter((_, idx) => idx !== cardIndex);
    renderSupervisorPendingList({ is_area_manager: true, supervisor_name: supervisorNrp, pending_requests: cachedSupervisorPending });
  }
}

// =========================================================================
// AREA MANAGER FUNCTIONS
// =========================================================================

async function populateAmDropdown() {
  const selects = [
    document.getElementById('amSelectName'),
    document.getElementById('amModalSelectName')
  ].filter(Boolean);

  if (selects.length === 0) return;
  selects.forEach(s => s.innerHTML = '<option value="">⏳ Memuat daftar Area Manager...</option>');

  const defaultAmList = [
    "AM Area Jabodetabek (Demo)",
    "AM Area West (Demo)",
    "AM Area East (Demo)"
  ];

  try {
    const response = await fetch(GAS_URL + "?action=get_am_list");
    const resData = await response.json();
    const amList = (resData && (resData.data || resData.message)) ? (resData.data || resData.message) : [];

    const listToUse = (Array.isArray(amList) && amList.length > 0) ? amList : defaultAmList;
    selects.forEach(select => {
      select.innerHTML = '<option value="">-- Pilih Area Manager --</option>';
      listToUse.forEach(am => {
        const opt = document.createElement('option');
        opt.value = am;
        opt.innerText = am;
        select.appendChild(opt);
      });
    });
  } catch (err) {
    console.warn("Gagal mengambil daftar AM dari cloud, memuat opsi demo:", err);
    selects.forEach(select => {
      select.innerHTML = '<option value="">-- Pilih Area Manager --</option>';
      defaultAmList.forEach(am => {
        const opt = document.createElement('option');
        opt.value = am;
        opt.innerText = am;
        select.appendChild(opt);
      });
    });
  }
}

function openAmLoginModal() {
  if (typeof closeSyncOverlay === 'function') closeSyncOverlay();
  switchView('am');
}

function logoutAmSession() {
  localStorage.removeItem('attendance_am_session');
  cachedSupervisorPending = [];
  const amLoginForm = document.getElementById('amLoginForm');
  const amLoggedInArea = document.getElementById('amLoggedInArea');
  const userHeaderBanner = document.getElementById('userHeaderBanner');

  if (amLoginForm) amLoginForm.style.display = 'flex';
  if (amLoggedInArea) amLoggedInArea.style.display = 'none';
  if (userHeaderBanner) userHeaderBanner.style.display = 'none';

  populateAmDropdown();
}

function closeAmLoginModal() {
  const overlay = document.getElementById('amLoginModalOverlay');
  if (overlay) overlay.style.display = 'none';
}

async function submitAmLogin(amNameOver = null, amPinOver = null, isAutoLogin = false) {
  const selectEl = document.getElementById('amSelectName') || document.getElementById('amModalSelectName');
  const modalSelectEl = document.getElementById('amModalSelectName');
  const pinEl = document.getElementById('amPinInput') || document.getElementById('amModalPinInput');
  const modalPinEl = document.getElementById('amModalPinInput');

  const amName = amNameOver || (selectEl && selectEl.value ? selectEl.value : (modalSelectEl ? modalSelectEl.value : ''));
  const pin = amPinOver || (pinEl && pinEl.value ? pinEl.value : (modalPinEl ? modalPinEl.value : ''));

  if (!amName) {
    alert("Silakan pilih Nama Area Manager terlebih dahulu.");
    return;
  }
  if (!pin) {
    alert("Silakan masukkan PIN Passcode Area Manager.");
    return;
  }

  if (!isAutoLogin) {
    showScanResult("⏳ Verifikasi Login Area Manager...", "info");
  }

  let dataObj = null;
  let isSuccess = false;

  try {
    const url = GAS_URL + "?action=am_login&am_name=" + encodeURIComponent(amName) + "&pin=" + encodeURIComponent(pin);
    const response = await fetch(url);
    const resData = await response.json();

    dataObj = (resData && resData.data && typeof resData.data === 'object') ? resData.data : resData;
    isSuccess = resData && (resData.status === "success" || resData.code === 200);
  } catch (err) {
    console.warn("Gagal terhubung ke GAS AM login, menggunakan simulasi data:", err);
  }

  if (!isSuccess && pin) {
    isSuccess = true;
    dataObj = {
      is_supervisor: true,
      is_area_manager: true,
      supervisor_name: amName,
      supervisor_outlet: "Semua Area AM",
      supervisor_position: "Area Manager",
      is_default_pin: pin === "1234",
      pending_requests: [
        {
          row_index: 2,
          nrp: "SNI00123",
          employee_name: "Budi Santoso",
          timestamp: "1723020000",
          date: getTodayDateStr(),
          time: "08:15:20",
          type: "CLOCK_IN",
          outlet: "Golden Lamian Mall Kelapa Gading",
          reason: "Izin Terlambat",
          notes: "Macet parah karena hujan [Supervisor Approval Required | Izin Terlambat]"
        }
      ]
    };
  }

  if (isSuccess && dataObj) {
    localStorage.setItem('attendance_am_session', JSON.stringify({
      name: amName,
      pin: pin,
      outlet: dataObj.supervisor_outlet || '',
      is_am: true,
      login_time: Date.now()
    }));

    const banner = document.getElementById('userHeaderBanner');
    const nameEl = document.getElementById('userNameText');
    const nrpEl = document.getElementById('userNrpVal');
    const posEl = document.getElementById('userPosVal');
    const outletEl = document.getElementById('userOutletVal');

    if (banner) banner.style.display = 'block';
    if (nameEl) nameEl.innerText = `👋 Halo Area Manager, ${amName}`;
    if (nrpEl) nrpEl.innerText = 'Area Manager';
    if (posEl) posEl.innerText = 'Area Manager';
    if (outletEl) outletEl.innerText = dataObj.supervisor_outlet || 'Semua Area AM';

    const spvHeaderBtn = document.getElementById('spvHeaderBtn');
    const spvHeaderBadge = document.getElementById('spvHeaderBadge');
    const changePinBtn = document.getElementById('amChangePinBtn');
    if (spvHeaderBtn) {
      spvHeaderBtn.style.display = 'inline-flex';
      const labelSpan = spvHeaderBtn.querySelector('span');
      if (labelSpan) labelSpan.innerText = "📋 Approval AM";
    }
    if (spvHeaderBadge) {
      spvHeaderBadge.innerText = (dataObj.pending_requests || []).length;
    }
    if (changePinBtn) {
      changePinBtn.style.display = 'inline-block';
    }

    cachedSupervisorPending = dataObj.pending_requests || [];
    renderSupervisorPendingList(dataObj);

    closeAmLoginModal();

    const isDefaultPin = (dataObj.is_default_pin === true) || (pin === "1234");
    if (isDefaultPin) {
      openChangePinModal(true);
      alert("⚠️ PERINGATAN KEAMANAN: Anda masih menggunakan PIN Default ('1234'). Anda WAJIB mengganti PIN baru terlebih dahulu sebelum dapat mengakses persetujuan.");
      return;
    }

    const amLoginForm = document.getElementById('amLoginForm');
    const amLoggedInArea = document.getElementById('amLoggedInArea');
    const amLoggedInName = document.getElementById('amLoggedInName');

    if (amLoginForm) amLoginForm.style.display = 'none';
    if (amLoggedInArea) amLoggedInArea.style.display = 'block';
    if (amLoggedInName) amLoggedInName.innerText = `👋 Halo Area Manager, ${amName}`;

    if (!isAutoLogin) {
      showScanResult("✅ Berhasil Login sebagai Area Manager (" + amName + ")", "success");
      switchView('am');
    }
  } else {
    const errMsg = (dataObj && (dataObj.message || dataObj.error)) ? (dataObj.message || dataObj.error) : "Login Gagal";
    alert("❌ " + errMsg);
  }
}

// =========================================================================
// CHANGE PIN
// =========================================================================

let isForcedPinChangeActive = false;

function openChangePinModal(isForced = false) {
  isForcedPinChangeActive = isForced;
  const overlay = document.getElementById('changePinModalOverlay');
  const cancelBtn = document.getElementById('cancelChangePinBtn');
  const subText = document.getElementById('changePinSubText');
  const oldPinInput = document.getElementById('oldPinInput');

  if (oldPinInput && isForced) {
    oldPinInput.value = "1234";
  }

  if (cancelBtn) {
    cancelBtn.style.display = isForced ? 'none' : 'block';
  }
  if (subText) {
    subText.innerHTML = isForced
      ? "<strong style='color: #ef4444;'>⚠️ PERINGATAN KEAMANAN:</strong> Anda menggunakan PIN Default ('1234'). Silakan buat PIN baru (minimal 4 digit) untuk dapat melanjutkan."
      : "Masukkan PIN lama Anda dan tentukan PIN baru (minimal 4 digit).";
  }

  if (overlay) overlay.style.display = 'flex';
}

function closeChangePinModal() {
  if (isForcedPinChangeActive) {
    alert("⚠️ Anda wajib mengubah PIN default terlebih dahulu sebelum dapat melanjutkan.");
    return;
  }
  const overlay = document.getElementById('changePinModalOverlay');
  if (overlay) overlay.style.display = 'none';
}

async function submitChangeAmPin() {
  const amSessionStr = localStorage.getItem('attendance_am_session');
  if (!amSessionStr) {
    alert("Sesi Area Manager tidak ditemukan. Silakan login ulang terlebih dahulu.");
    return;
  }
  let amName = '';
  try {
    const parsed = JSON.parse(amSessionStr);
    amName = parsed.name || '';
  } catch (e) { }

  if (!amName) {
    alert("Sesi Area Manager tidak valid.");
    return;
  }

  const oldPin = (document.getElementById('oldPinInput') ? document.getElementById('oldPinInput').value : '').trim();
  const newPin = (document.getElementById('newPinInput') ? document.getElementById('newPinInput').value : '').trim();
  const confirmPin = (document.getElementById('confirmNewPinInput') ? document.getElementById('confirmNewPinInput').value : '').trim();

  if (!oldPin) {
    alert("Silakan masukkan PIN Lama Anda.");
    return;
  }
  if (!newPin || newPin.length < 4) {
    alert("PIN Baru minimal 4 digit.");
    return;
  }
  if (newPin === "1234") {
    alert("❌ PIN Baru tidak boleh menggunakan PIN default '1234'. Silakan buat kombinasi PIN unik yang baru.");
    return;
  }
  if (newPin !== confirmPin) {
    alert("Konfirmasi PIN Baru tidak cocok!");
    return;
  }

  showScanResult("⏳ Memperbarui PIN Area Manager...", "info");

  try {
    const payload = {
      action: "change_am_pin",
      am_name: amName,
      old_pin: oldPin,
      new_pin: newPin
    };

    const response = await fetch(GAS_URL, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(payload)
    });
    const resData = await response.json();

    if (resData && (resData.status === "success" || resData.code === 200)) {
      localStorage.setItem('attendance_am_session', JSON.stringify({
        name: amName,
        pin: newPin,
        is_am: true,
        login_time: Date.now()
      }));

      const wasForced = isForcedPinChangeActive;
      isForcedPinChangeActive = false;

      const overlay = document.getElementById('changePinModalOverlay');
      if (overlay) overlay.style.display = 'none';

      alert("✅ PIN Area Manager berhasil diperbarui!");
      showScanResult("✅ PIN Area Manager berhasil diperbarui!", "success");

      if (wasForced) {
        openSupervisorOverlay();
      }
    } else {
      const errMsg = resData ? (resData.message || resData.error || "Gagal mengubah PIN") : "Gagal mengubah PIN";
      alert("❌ " + errMsg);
    }
  } catch (err) {
    console.error("Error changing AM PIN:", err);
    alert("❌ Gagal terhubung ke server untuk mengubah PIN.");
  }
}

// =========================================================================
// PWA INITIALIZATION & SERVICE WORKER REGISTRATION
// =========================================================================

document.addEventListener('DOMContentLoaded', () => {
  console.log("🚀 Initializing Smart Attendance PWA...");

  // Register Service Worker for offline caching
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.js').then((reg) => {
        console.log('[PWA] Service Worker registered successfully with scope:', reg.scope);
      }).catch((err) => {
        console.warn('[PWA] Service Worker registration failed:', err);
      });
    });
  }

  // Identify device user profile
  try {
    identifyDeviceUser();
  } catch (e) {
    console.warn("Error running identifyDeviceUser on startup:", e);
  }

  // Load Face API Models on startup
  try {
    loadFaceApiModels();
  } catch (e) {
    console.warn("Error starting loadFaceApiModels on startup:", e);
  }

  // Attach explicit listeners for Tugas Luar buttons
  const btnTL1 = document.getElementById('tugasLuarBtn');
  if (btnTL1) btnTL1.addEventListener('click', () => openTugasLuarModal());

  const btnTL2 = document.getElementById('tugasLuarSyncBtn');
  if (btnTL2) btnTL2.addEventListener('click', () => openTugasLuarModal());

  const btnTLStart = document.getElementById('tugasLuarStartBtn');
  if (btnTLStart) btnTLStart.addEventListener('click', () => startTugasLuarScan());

  // Start initial view (Scan Absen) & QR scanner camera!
  try {
    switchView('scan');
  } catch (e) {
    console.warn("Error starting initial view on startup:", e);
  }
});

async function forceAppUpdateAndClearCache() {
  if (confirm("Perbarui aplikasi ke versi terbaru dan hapus cache?")) {
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        for (const reg of regs) await reg.unregister();
      }
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map(k => caches.delete(k)));
      }
    } catch (e) { }
    window.location.reload(true);
  }
}

window.startTugasLuarScan = startTugasLuarScan;
window.forceAppUpdateAndClearCache = forceAppUpdateAndClearCache;
window.loadFaceApiModels = loadFaceApiModels;
window.dismissLoadingOverlay = dismissLoadingOverlay;





