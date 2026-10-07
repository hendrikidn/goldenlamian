/**
 * @OnlyCurrentDoc
 * Google Apps Script (GAS) - Serverless Backend Absensi QR Code (Single Spreadsheet)
 * 
 * Petunjuk Instalasi:
 * 1. Buka Google Sheet Absensi Anda (yang sekarang berisi tab: "attendance_records", "Outlets", "MP Database", dan "Face_Embedding").
 * 2. Klik Extensions > Apps Script.
 * 3. Hapus kode lama, tempel seluruh kode ini ke editor.
 * 4. Klik Deploy > Manage Deployments atau New Deployment, lalu deploy ulang sebagai Web App.
 */

// Konfigurasi Nama Tab
var TAB_ATTENDANCE = "attendance_records";
var TAB_OUTLETS = "Outlets";
var TAB_EMPLOYEES = "MP Database"; 
var TAB_FACE_EMBEDDING = "Face_Embedding"; // Tab terpisah khusus untuk data embedding wajah
var TAB_OUTLET_SCHEDULE = "Outlet Schedule"; // Tab Jadwal Shift Outlet

// Konfigurasi Validasi Wajah Server-Side
// Set ke false untuk BYPASS pencocokan Euclidean Distance wajah di cloud server (SANGAT DIREKOMENDASIKAN)
// Catatan: Keamanan absensi tetap terlindungi penuh oleh:
//   1. Validasi QR Code TOTP dinamis layar PC (refresh otomatis tiap menit)
//   2. Validasi Lokasi GPS Geofence Outlet
//   3. Validasi Binding Perangkat HP (1 HP hanya untuk 1 karyawan)
//   4. Liveness Detection di HP (wajib deteksi wajah manusia & tersenyum sebelum menu absen aktif)
var ENABLE_SERVER_FACE_MATCH = true; 
var FACE_MATCH_THRESHOLD = 0.90; // Ambang batas jarak toleran (aman dari false rejection tapi memblokir orang lain)

// ============================================================================
// OPTIMASI PEMBACAAN SHEET
// ============================================================================

// Memo per-eksekusi: dalam satu doGet/doPost tiap tab referensi (MP Database, Outlets, Outlet Schedule)
// hanya dibaca sekali walau dipakai beberapa fungsi. Direset di awal setiap request.
var _sheetValuesMemo = {};

function resetExecutionMemo_() {
  _sheetValuesMemo = {};
}

function getSheetValues_(sheet) {
  var key = sheet.getName();
  if (!Object.prototype.hasOwnProperty.call(_sheetValuesMemo, key)) {
    _sheetValuesMemo[key] = sheet.getDataRange().getValues();
  }
  return _sheetValuesMemo[key];
}

// Pembaca tanggal sel kolom 'date' attendance_records -> "yyyy-MM-dd".
// Sel bertipe Date di-cache per timestamp agar puluhan ribu baris tidak memanggil Utilities.formatDate satu per satu.
function makeAttendanceDateNormalizer_(tz) {
  var cache = {};
  return function (rawDateVal) {
    if (rawDateVal instanceof Date) {
      var t = rawDateVal.getTime();
      if (!Object.prototype.hasOwnProperty.call(cache, t)) {
        cache[t] = Utilities.formatDate(rawDateVal, tz, "yyyy-MM-dd");
      }
      return cache[t];
    }
    if (rawDateVal) return String(rawDateVal).replace(/^'+/, '').substring(0, 10);
    return "";
  };
}

// Pembaca tab attendance_records yang hemat. Tab ini terus bertambah (puluhan ribu baris per bulan) dan dulu dibaca
// PENUH (getDataRange) dua kali di setiap absen, di dalam script lock. Sekarang:
//   - sheet kecil (<= ATTENDANCE_FULL_READ_MAX_ROWS): baca penuh sekali (memo per-request), seperti sebelumnya;
//   - sheet besar: baca HANYA kolom 'date' (memo per-request), tentukan baris yang tanggalnya memenuhi syarat, lalu
//     baca hanya rentang baris itu dan hanya kolom yang dibutuhkan.
// Hasilnya persis sama dengan membaca seluruh sheet untuk urutan baris apa pun (tidak bergantung pada kronologi);
// kecepatan terbaik tercapai bila baris kronologis (selalu begitu lewat appendRow), karena rentangnya kecil.
var ATTENDANCE_FULL_READ_MAX_ROWS = 1500;

function getAttendanceReader_(attendanceSheet, tz) {
  var key = "__attendance_reader__" + attendanceSheet.getName();
  if (Object.prototype.hasOwnProperty.call(_sheetValuesMemo, key)) return _sheetValuesMemo[key];

  var reader = {
    sheet: attendanceSheet,
    idx: { nrp: 0, date: 3, type: 5 },
    lastRow: attendanceSheet.getLastRow(),
    lastCol: attendanceSheet.getLastColumn(),
    full: null,
    dates: null,
    normalizeDate: makeAttendanceDateNormalizer_(tz)
  };

  if (reader.lastRow > 1 && reader.lastCol > 0) {
    var headers;
    if (reader.lastRow <= ATTENDANCE_FULL_READ_MAX_ROWS) {
      reader.full = getSheetValues_(attendanceSheet);
      headers = reader.full[0];
    } else {
      headers = attendanceSheet.getRange(1, 1, 1, reader.lastCol).getValues()[0];
    }
    for (var h = 0; h < headers.length; h++) {
      var hName = String(headers[h]).toLowerCase().trim();
      if (hName === "nrp") reader.idx.nrp = h;
      if (hName === "date") reader.idx.date = h;
      if (hName === "type") reader.idx.type = h;
    }
  }

  _sheetValuesMemo[key] = reader;
  return reader;
}

// Mengembalikan { rows, offset, idx }: baris-baris (urut seperti di sheet) yang tanggalnya lolos dateMatches(),
// nilai kolom c ada di row[c - offset]. neededColIdx = kolom selain 'date' yang akan dibaca pemanggil.
function selectAttendanceRows_(reader, dateMatches, neededColIdx) {
  var idx = reader.idx;
  var result = { rows: [], offset: 0, idx: idx };
  if (reader.lastRow <= 1 || reader.lastCol < 1) return result;

  if (reader.full) {
    for (var i = 1; i < reader.full.length; i++) {
      if (dateMatches(reader.normalizeDate(reader.full[i][idx.date]))) result.rows.push(reader.full[i]);
    }
    return result;
  }

  if (idx.date >= reader.lastCol) return result; // kolom date tidak ada -> tidak ada baris yang cocok

  if (!reader.dates) {
    var dateCol = reader.sheet.getRange(2, idx.date + 1, reader.lastRow - 1, 1).getValues();
    reader.dates = new Array(dateCol.length);
    for (var r = 0; r < dateCol.length; r++) reader.dates[r] = reader.normalizeDate(dateCol[r][0]);
  }

  var first = -1, last = -1;
  for (var k = 0; k < reader.dates.length; k++) {
    if (dateMatches(reader.dates[k])) {
      if (first === -1) first = k;
      last = k;
    }
  }
  if (first === -1) return result;

  var cols = neededColIdx.concat([idx.date]);
  var minC = Math.min.apply(null, cols);
  var maxC = Math.min(Math.max.apply(null, cols), reader.lastCol - 1);
  if (minC > maxC) return result;

  var span = reader.sheet.getRange(first + 2, minC + 1, last - first + 1, maxC - minC + 1).getValues();
  for (var j = 0; j < span.length; j++) {
    if (dateMatches(reader.dates[first + j])) result.rows.push(span[j]);
  }
  result.offset = minC;
  return result;
}

/**
 * Menangani HTTP GET Request dari Dashboard PC Outlet atau PWA Ponsel
 */
function doGet(e) {
  resetExecutionMemo_();
  try {
    var params = (e && e.parameter) ? e.parameter : {};
    var action = params.action;
    var outlet = params.outlet || params.outlet_id;

    if (!action) {
      return jsonResponse("success", "API Attendance System Active", 200);
    }

    // 0. Aksi Mendapatkan Daftar Shift Outlet dari Tab 'Outlet Schedule'
    if (action === "get_outlet_shifts") {
      return getOutletShifts(outlet);
    }

    // 0.1 Aksi Verifikasi Validitas NRP (Pengecekan di MP Database & Face_Embedding)
    if (action === "verify_nrp") {
      var checkNrp = params.nrp;
      if (!checkNrp) {
        return jsonResponse("error", "NRP wajib diisi.", 400);
      }
      var ssVerify = SpreadsheetApp.getActiveSpreadsheet();
      var empSheetVerify = ssVerify.getSheetByName(TAB_EMPLOYEES);
      if (!empSheetVerify) {
        return jsonResponse("error", "Tab database master karyawan '" + TAB_EMPLOYEES + "' tidak ditemukan.", 500);
      }
      var empName = getEmployeeNameByNRP(empSheetVerify, checkNrp);
      if (!empName || empName.indexOf("Unknown Staff") === 0) {
        return jsonResponse("error", "NRP " + checkNrp + " tidak terdaftar dalam database master karyawan.", 404);
      }
      var empRoleInfo = getEmployeeRoleByNRP(empSheetVerify, checkNrp);
      var faceSheetVerify = ssVerify.getSheetByName(TAB_FACE_EMBEDDING);
      var faceRecordVerify = faceSheetVerify ? getFaceRecordByNRP(faceSheetVerify, checkNrp) : null;
      var hasFace = !!(faceRecordVerify && faceRecordVerify.embedding);

      return jsonResponse("success", {
        found: true,
        nrp: checkNrp,
        name: empName,
        position: empRoleInfo.position || "",
        outlet: empRoleInfo.outlet || "",
        is_supervisor: empRoleInfo.isSupervisor,
        is_area_manager: empRoleInfo.isAreaManager,
        has_face_registered: hasFace,
        device_id: faceRecordVerify ? (faceRecordVerify.deviceId || "") : ""
      }, 200);
    }

    // 0a. Aksi Mendapatkan Daftar Area Manager & Login AM via PIN
    if (action === "get_am_list") {
      return getAreaManagerList();
    }
    if (action === "am_login") {
      var amNameReq = params.am_name || params.name;
      var amPinReq = params.pin;
      return handleAreaManagerLogin(amNameReq, amPinReq);
    }
    if (action === "change_am_pin") {
      var amNameReq = params.am_name || params.name;
      var oldPinReq = params.old_pin;
      var newPinReq = params.new_pin;
      return handleChangeAmPin(amNameReq, oldPinReq, newPinReq);
    }

    // 0a1. Aksi Mendapatkan Peran & Daftar Pengajuan Persetujuan Supervisor / AM
    if (action === "get_supervisor_pending" || action === "check_supervisor") {
      var spvNrp = params.nrp || params.am_name || params.name;
      if (!spvNrp) {
        return jsonResponse("error", "NRP / Nama Supervisor wajib diisi.", 400);
      }
      return getSupervisorPendingInfo(spvNrp);
    }

    // 0a1. Aksi Mendapatkan Data User berdasarkan Device ID
    if (action === "get_user_by_device_id" || action === "identify_device") {
      var reqDevId = params.device_id || params.deviceId;
      var reqNrp = params.nrp;
      return getUserByDeviceId(reqDevId, reqNrp);
    }

    // 0a2. Cek Status Unbind Device untuk NRP tertentu (dipakai PWA sebelum absensi)
    if (action === "check_unbind_status") {
      var ubNrp = params.nrp;
      if (!ubNrp) {
        return jsonResponse("error", "NRP wajib diisi.", 400);
      }
      return checkUnbindStatus(ubNrp);
    }

    // 0b. Aksi Mendapatkan Konfigurasi Outlet (Secret Key, Radius, Koordinat, PWA URL)
    if (action === "get_outlet_config") {
      if (!outlet) {
        return jsonResponse("error", "Outlet wajib diisi.", 400);
      }
      var ssConfig = SpreadsheetApp.getActiveSpreadsheet();
      var outletSheetConfig = ssConfig.getSheetByName(TAB_OUTLETS);
      if (!outletSheetConfig) {
        return jsonResponse("error", "Tab 'Outlets' tidak ditemukan.", 404);
      }
      var configData = getOutletConfig(outletSheetConfig, outlet);
      if (!configData) {
        return jsonResponse("error", "Outlet '" + outlet + "' tidak ditemukan.", 404);
      }
      return jsonResponse("success", configData, 200);
    }

    // 0c. Aksi Mendapatkan Daftar Seluruh Nama Outlet yang Terdaftar (untuk Dropdown Setup)
    if (action === "get_all_outlets") {
      var ssAll = SpreadsheetApp.getActiveSpreadsheet();
      var outletSheetAll = ssAll.getSheetByName(TAB_OUTLETS);
      if (!outletSheetAll) {
        return jsonResponse("success", [], 200);
      }
      var dataOutlets = outletSheetAll.getDataRange().getValues();
      var outletsList = [];
      if (dataOutlets.length > 1) {
        var oColIdx = 0;
        var headerRowIdx = 0;
        var headerFound = false;
        
        for (var r = 0; r < Math.min(dataOutlets.length, 5); r++) {
          for (var ho = 0; ho < dataOutlets[r].length; ho++) {
            var hoName = String(dataOutlets[r][ho]).toLowerCase().trim();
            if (hoName === "outlet" || hoName === "nama outlet" || hoName === "outlet_name" || hoName === "outlet_id" || hoName === "cabang" || hoName === "nama cabang") {
              oColIdx = ho;
              headerRowIdx = r;
              headerFound = true;
              break;
            }
          }
          if (headerFound) break;
        }

        for (var io = headerRowIdx + 1; io < dataOutlets.length; io++) {
          var valOut = String(dataOutlets[io][oColIdx]).trim();
          if (valOut && outletsList.indexOf(valOut) === -1) {
            outletsList.push(valOut);
          }
        }
      }
      return jsonResponse("success", outletsList, 200);
    }
    
    // 1. Aksi Mendapatkan Absensi Hari Ini (untuk Layar Monitor PC)
    if (action === "get_today_attendance") {
      if (!outlet) {
        return jsonResponse("error", "Outlet wajib diisi.", 400);
      }
      
      var ss = SpreadsheetApp.getActiveSpreadsheet();
      var attendanceSheet = ss.getSheetByName(TAB_ATTENDANCE);
      if (!attendanceSheet) {
        return jsonResponse("success", [], 200); 
      }
      
      var data = attendanceSheet.getDataRange().getValues();
      var todayStr = Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), "yyyy-MM-dd");
      var list = [];
      
      var nrpIdx = 0;      
      var nameIdx = 1;     
      var timestampIdx = 2;
      var dateIdx = 3;     
      var timeIdx = 4;     
      var typeIdx = 5;     
      var outletIdx = 8;   
      
      if (data.length > 0) {
        var headers = data[0];
        for (var h = 0; h < headers.length; h++) {
          var hName = String(headers[h]).toLowerCase().trim();
          if (hName === "nrp") nrpIdx = h;
          if (hName === "employee_name") nameIdx = h;
          if (hName === "timestamp") timestampIdx = h;
          if (hName === "date") dateIdx = h;
          if (hName === "time") timeIdx = h;
          if (hName === "type") typeIdx = h;
          if (hName === "outlet" || hName === "outlet_id" || hName === "nama outlet") outletIdx = h;
        }
      }
      
      for (var i = 1; i < data.length; i++) {
        var recOutlet = String(data[i][outletIdx]).trim();
        var rawDateVal = data[i][dateIdx];
        var rawTimeVal = data[i][timeIdx];
        var rawTimestampVal = data[i][timestampIdx];

        var recDateStr = "";
        if (rawDateVal instanceof Date) {
          recDateStr = Utilities.formatDate(rawDateVal, ss.getSpreadsheetTimeZone(), "yyyy-MM-dd");
        } else if (rawDateVal) {
          recDateStr = String(rawDateVal).replace(/^'+/, '').substring(0, 10);
        }
        
        if (recOutlet.toLowerCase() === String(outlet).trim().toLowerCase() && recDateStr === todayStr) {
          var formattedTimeStr = "";
          if (rawTimeVal instanceof Date) {
            formattedTimeStr = Utilities.formatDate(rawTimeVal, ss.getSpreadsheetTimeZone(), "HH:mm:ss");
          } else if (rawTimeVal) {
            formattedTimeStr = String(rawTimeVal).replace(/^'+/, '');
            var matchTime = formattedTimeStr.match(/(\d{2}:\d{2}:\d{2})/);
            if (matchTime) {
              formattedTimeStr = matchTime[1];
            } else if (formattedTimeStr.indexOf("1899") !== -1) {
              var parts = formattedTimeStr.split(" ");
              for (var p = 0; p < parts.length; p++) {
                if (parts[p].match(/^\d{2}:\d{2}:\d{2}$/)) {
                  formattedTimeStr = parts[p];
                  break;
                }
              }
            }
          }

          var formattedFullTimestamp = "";
          if (rawTimestampVal instanceof Date) {
            formattedFullTimestamp = Utilities.formatDate(rawTimestampVal, ss.getSpreadsheetTimeZone(), "yyyy-MM-dd HH:mm:ss");
          } else if (rawTimestampVal) {
            formattedFullTimestamp = String(rawTimestampVal).replace(/^'+/, '').replace('T', ' ');
          }

          // Jika formattedFullTimestamp mengandung "1899" atau kosong, bentuk ulang dari recDateStr & formattedTimeStr
          if (!formattedFullTimestamp || formattedFullTimestamp.indexOf("1899") !== -1) {
            formattedFullTimestamp = recDateStr + " " + (formattedTimeStr || "00:00:00");
          }

          list.push({
            nrp: String(data[i][nrpIdx]),
            name: String(data[i][nameIdx]),
            time: formattedTimeStr,
            timestamp: formattedFullTimestamp,
            type: String(data[i][typeIdx])
          });
        }
      }
      
      list.reverse();
      return jsonResponse("success", list, 200);
    }
    
    // 2. Aksi Mengunduh Data Wajah Terdaftar (untuk Sinkronisasi Browser Baru Ponsel Karyawan)
    if (action === "get_face_embedding") {
      var nrp = e.parameter.nrp;
      var reqDeviceId = e.parameter.device_id;
      if (!nrp) {
        return jsonResponse("error", "NRP wajib diisi.", 400);
      }
      
      var ss = SpreadsheetApp.getActiveSpreadsheet();
      var faceSheet = ss.getSheetByName(TAB_FACE_EMBEDDING);
      if (!faceSheet) {
        return jsonResponse("error", "Tab '" + TAB_FACE_EMBEDDING + "' belum ada. Wajah belum terdaftar di cloud.", 404);
      }
      
      var faceRecord = getFaceRecordByNRP(faceSheet, nrp);
      if (!faceRecord || !faceRecord.embedding) {
        return jsonResponse("error", "Data pendaftaran wajah untuk NRP " + nrp + " belum terdaftar di cloud. Silakan melakukan Registrasi Wajah terlebih dahulu.", 404);
      }

      // Jika belum ada deviceId di cloud atau jika deviceId kosong, update dengan reqDeviceId
      if (reqDeviceId && !faceRecord.deviceId) {
        try {
          var data = faceSheet.getDataRange().getValues();
          // Deteksi kolom Device_ID secara dinamis (BUKAN hardcoded)
          var devIdColDyn = 2; // default index 2 = kolom C
          if (data.length > 0) {
            var hdrs = data[0];
            for (var hh = 0; hh < hdrs.length; hh++) {
              var hhName = String(hdrs[hh]).toLowerCase().trim();
              if (hhName === "device_id" || hhName === "device id" || hhName === "deviceid") {
                devIdColDyn = hh;
                break;
              }
            }
          }
          for (var i = 1; i < data.length; i++) {
            if (String(data[i][0]).trim().toLowerCase() === String(nrp).trim().toLowerCase()) {
              faceSheet.getRange(i + 1, devIdColDyn + 1).setValue(reqDeviceId);
              faceRecord.deviceId = reqDeviceId;
              break;
            }
          }
        } catch (e) {}
      }

      // Jika reqDeviceId beda tapi faceRecord ada, izinkan sync dan return device_id resmi
      var responseObj = {
        status: "success",
        message: faceRecord.embedding,
        device_id: faceRecord.deviceId || reqDeviceId || ""
      };
      
      return ContentService.createTextOutput(JSON.stringify(responseObj))
        .setMimeType(ContentService.MimeType.JSON);
    }
    
    // 3. Aksi Unbind Device (Reset HP Karyawan oleh Admin)
    if (action === "unbind_device") {
      var nrpToUnbind = e.parameter.nrp;
      if (!nrpToUnbind) {
        return jsonResponse("error", "NRP wajib diisi.", 400);
      }
      return handleUnbindDevice(nrpToUnbind);
    }

    return jsonResponse("success", [], 200);
  } catch (error) {
    return jsonResponse("error", "Terjadi kesalahan internal GET: " + error.toString(), 500);
  }
}

/**
 * Menangani HTTP POST Request dari Ponsel Karyawan
 */
function doPost(e) {
  resetExecutionMemo_();
  if (!e || !e.postData || !e.postData.contents) {
    return jsonResponse("error", "Payload POST tidak ditemukan atau kosong.", 400);
  }

  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    return jsonResponse("error", "Server sedang sibuk memproses absensi lain. Silakan coba 2 detik lagi.", 530);
  }

  try {
    var requestData;
    try {
      requestData = JSON.parse(e.postData.contents);
    } catch (parseErr) {
      return jsonResponse("error", "Format JSON payload tidak valid: " + parseErr.toString(), 400);
    }

    if (!requestData || typeof requestData !== "object") {
      return jsonResponse("error", "Data payload tidak berformat objek valid.", 400);
    }

    var action = requestData.action;
    
    // 1. Aksi Registrasi Wajah Cloud (menyimpan embedding wajah ke tab Face_Embedding)
    if (action === "register_face") {
      return handleRegisterFace(requestData);
    }

    // 1b. Aksi Permintaan Unbind Device dari Karyawan (Ganti HP)
    if (action === "request_unbind_device") {
      return handleRequestUnbindDevice(requestData);
    }

    // 1c. Aksi Unbind Device (Reset Device oleh Admin)
    if (action === "unbind_device") {
      return handleUnbindDevice(requestData.nrp || requestData.nrpToUnbind);
    }

    // 1d. Aksi Keputusan Supervisor (Approve / Reject Pengajuan Absensi Staff)
    if (action === "update_approval_status" || action === "supervisor_decision") {
      return handleSupervisorDecision(requestData);
    }
    if (action === "change_am_pin") {
      var amNameReq = requestData.am_name || requestData.name;
      var oldPinReq = requestData.old_pin;
      var newPinReq = requestData.new_pin;
      return handleChangeAmPin(amNameReq, oldPinReq, newPinReq);
    }
    var nrp = requestData.nrp;
    var outlet = requestData.outlet || requestData.outlet_id;
    var totpToken = requestData.totp_token;
    var scanTimestamp = Number(requestData.timestamp);
    var latUser = Number(requestData.latitude);
    var lngUser = Number(requestData.longitude);
    var isFaceVerified = requestData.face_verified;
    var isLivenessPassed = requestData.liveness_passed;
    var reqDeviceId = requestData.device_id;
    
    var isTugasLuarMode = requestData.is_tugas_luar === true || String(requestData.is_tugas_luar).toLowerCase() === "true" || requestData.action === "clock_in_tugas_luar";

    if (!nrp || (!isTugasLuarMode && (!outlet || !totpToken)) || !scanTimestamp || isNaN(latUser) || isNaN(lngUser)) {
      return jsonResponse("error", "Data parameter absensi tidak lengkap.", 400);
    }
    
    if (!isFaceVerified || !isLivenessPassed) {
      return jsonResponse("error", "Verifikasi wajah atau deteksi liveness gagal.", 403);
    }

    // Validasi Waktu Expired Token QR Code
    // Jika antrean offline: Wajib disinkronkan pada tanggal hari yang sama (tidak boleh melintasi tanggal scan)
    // Jika live online: Toleransi maksimal 2 jam (7200 detik)
    var isOfflineQueued = requestData.is_offline_queued === true;
    var currentTimestamp = Math.floor(Date.now() / 1000);
    
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var tz = ss.getSpreadsheetTimeZone();
    var todayDateStr = Utilities.formatDate(new Date(), tz, "yyyy-MM-dd");
    var scanDateStr = Utilities.formatDate(new Date(scanTimestamp * 1000), tz, "yyyy-MM-dd");

    if (isOfflineQueued) {
      if (scanDateStr !== todayDateStr) {
        return jsonResponse("error", "Absensi ditolak! Antrean absensi offline harus disinkronkan pada hari yang sama (tidak boleh melewati tanggal scan).", 400);
      }
    } else if (!isTugasLuarMode) {
      var timeDiff = Math.abs(currentTimestamp - scanTimestamp);
      if (timeDiff > 7200) { 
        return jsonResponse("error", "Waktu scan sudah terlalu lama (kedaluwarsa). Silakan lakukan scan ulang.", 400);
      }
    }

    // Pencegahan TOTP Replay Attack (Hanya jika bukan Tugas Luar Event)
    if (!isTugasLuarMode) {
      var cache = CacheService.getScriptCache();
      var cacheKey = "TOTP_USED_" + String(outlet).trim().toLowerCase() + "_" + String(totpToken).trim();
      if (cache.get(cacheKey)) {
        return jsonResponse("error", "Absensi ditolak! Kode QR ini sudah pernah digunakan oleh staf lain. Silakan tunggu QR Code ter-refresh di layar monitor.", 403);
      }
    }

    // 1. Validasi Keberadaan Karyawan di Tab Face_Embedding Cloud (Wajib Terdaftar)
    var faceSheet = ss.getSheetByName(TAB_FACE_EMBEDDING);
    if (!faceSheet) {
      return jsonResponse("error", "Absensi ditolak! Database tab '" + TAB_FACE_EMBEDDING + "' tidak ditemukan di spreadsheet.", 500);
    }
    
    var faceRecord = getFaceRecordByNRP(faceSheet, nrp);
    if (!faceRecord || !faceRecord.embedding) {
      return jsonResponse("error", "Absensi ditolak! NRP " + nrp + " belum terdaftar di tab Face_Embedding cloud. Silakan melakukan registrasi wajah terlebih dahulu.", 403);
    }
    
    // 2. Validasi Binding Perangkat (Device ID)
    if (faceRecord.deviceId && reqDeviceId) {
      var savedDevId = String(faceRecord.deviceId).trim().toLowerCase();
      var currentDevId = String(reqDeviceId).trim().toLowerCase();
      
      var isDevMatch = (savedDevId === currentDevId);
      if (!isDevMatch) {
        // Ekstrak hash fingerprint hardware (misal DEV-ID-HASH-UUID atau DEV-FP-HASH)
        var savedParts = savedDevId.split('-');
        var currentParts = currentDevId.split('-');
        var savedHash = (savedParts.length >= 3) ? savedParts[2] : (savedParts.length >= 2 ? savedParts[1] : "");
        var currentHash = (currentParts.length >= 3) ? currentParts[2] : (currentParts.length >= 2 ? currentParts[1] : "");
        
        if (savedHash && currentHash && savedHash.length >= 6 && savedHash === currentHash) {
          isDevMatch = true; // Hardware HP sama persis!
        }
      }

      if (!isDevMatch) {
        return jsonResponse("error", "Absensi ditolak! Perangkat ini berbeda dari perangkat resmi terdaftar untuk NRP " + nrp + ".", 403);
      }
    }

    // 3. Validasi Match Wajah Live vs Cloud Face_Embedding (Server-side Euclidean Distance)
    if (ENABLE_SERVER_FACE_MATCH) {
      var liveEmbedding = requestData.face_embedding;
      if (liveEmbedding && Array.isArray(liveEmbedding) && liveEmbedding.length > 0) {
        var faceDist = calculateEuclideanDistance(liveEmbedding, faceRecord.embedding);
        if (faceDist > FACE_MATCH_THRESHOLD) {
          return jsonResponse("error", "Absensi ditolak! Wajah yang dipindai tidak cocok dengan data wajah terdaftar di cloud untuk NRP " + nrp + " (Jarak: " + faceDist.toFixed(2) + " > " + FACE_MATCH_THRESHOLD + ").", 403);
        }
      } else {
        return jsonResponse("error", "Absensi ditolak! Sampel verifikasi wajah live tidak terkirim.", 400);
      }
    }

    // 4. Validasi Lokasi & Geofence
    var outletData = { latitude: 0, longitude: 0, radius: 999999, secret: "" };
    var roundedDistance = 0;

    if (!isTugasLuarMode) {
      var outletSheet = ss.getSheetByName(TAB_OUTLETS);
      if (!outletSheet) {
        return jsonResponse("error", "Tab konfigurasi 'Outlets' tidak ditemukan di database spreadsheet.", 500);
      }
      
      outletData = getOutletConfig(outletSheet, outlet);
      if (!outletData) {
        return jsonResponse("error", "Outlet '" + outlet + "' tidak terdaftar.", 404);
      }
      
      // Blokir Ketat: GPS Mati (lat: 0, lng: 0)
      if (latUser === 0 && lngUser === 0) {
        return jsonResponse("error", "Absensi ditolak! GPS pada perangkat Anda tidak aktif. Wajib mengaktifkan GPS HP Anda.", 403);
      }

      // Blokir Ketat: Akurasi GPS Buruk / Fake GPS
      var accuracyUser = Number(requestData.accuracy) || 0;
      if (accuracyUser > 150) {
        return jsonResponse("error", "Absensi ditolak! Akurasi GPS terlalu buruk (" + Math.round(accuracyUser) + "m) atau terdeteksi penggunaan Fake GPS.", 403);
      }

      // Blokir Ketat Jarak Berdasarkan Kolom 'Radius' di Tab 'Outlets'
      if (outletData && outletData.latitude !== 0) {
        var distance = calculateDistance(latUser, lngUser, outletData.latitude, outletData.longitude);
        roundedDistance = Math.round(distance);
        if (roundedDistance > outletData.radius) {
          return jsonResponse("error", "Absensi ditolak! Anda berada diluar lokasi outlet (" + roundedDistance + "m). Jarak maksimal yang diizinkan adalah " + outletData.radius + " meter.", 403);
        }
      }
      
      var isOtpValid = verifyTOTP(totpToken, outletData.secret, scanTimestamp);
      if (!isOtpValid) {
        return jsonResponse("error", "Token QR Code tidak valid atau sudah kedaluwarsa. Silakan scan ulang.", 401);
      }
    } else {
      // Validasi GPS dasar untuk Tugas Luar Event
      if (latUser === 0 && lngUser === 0) {
        return jsonResponse("error", "Absensi ditolak! GPS pada perangkat Anda tidak aktif. Wajib mengaktifkan GPS HP Anda.", 403);
      }
      var accuracyUser = Number(requestData.accuracy) || 0;
      if (accuracyUser > 150) {
        return jsonResponse("error", "Absensi ditolak! Akurasi GPS terlalu buruk (" + Math.round(accuracyUser) + "m) atau terdeteksi penggunaan Fake GPS.", 403);
      }
      if (!outlet) outlet = "TUGAS_LUAR";
    }
    
    var employeeSheet = ss.getSheetByName(TAB_EMPLOYEES);
    var employeeName = "Unknown Staff";
    var empRole = { isSupervisor: false, position: "", outlet: "", name: "" };
    if (employeeSheet) {
      employeeName = getEmployeeNameByNRP(employeeSheet, nrp);
      empRole = getEmployeeRoleByNRP(employeeSheet, nrp);
    }
    
    var attendanceSheet = ss.getSheetByName(TAB_ATTENDANCE);
    if (!attendanceSheet) {
      attendanceSheet = ss.insertSheet(TAB_ATTENDANCE);
      attendanceSheet.appendRow([
        "NRP", "employee_name", "timestamp", "date", "time", 
        "type", "timezone", "outlet", "notes", "distance_meters", "working_hour", "gps_accuracy"
      ]);
    }

    var dateObj = new Date(scanTimestamp * 1000);
    var formattedDate = Utilities.formatDate(dateObj, ss.getSpreadsheetTimeZone(), "yyyy-MM-dd");
    var formattedTime = Utilities.formatDate(dateObj, ss.getSpreadsheetTimeZone(), "HH:mm:ss");
    var formattedTimestamp = Utilities.formatDate(dateObj, ss.getSpreadsheetTimeZone(), "yyyy-MM-dd'T'HH:mm:ss");
    
    var attendanceType = requestData.attendance_type || "CLOCK_IN";
    var notes = requestData.notes || ("Absen QR via PWA (DevID: " + (reqDeviceId || "N/A") + ")");
    
    // Tag audit jika lokasi diluar radius atau GPS nonaktif
    if (outletData && outletData.radius && roundedDistance > outletData.radius) {
      notes += " [AUDIT: Diluar radius (" + roundedDistance + "m)]";
    } else if (latUser === 0 && lngUser === 0) {
      notes += " [AUDIT: GPS Nonaktif]";
    }

    // Tagging Penugasan Silang Outlet (Cross-Outlet Duty) - Membutuhkan AM Approval
    var homeOutlet = empRole ? String(empRole.outlet || "").trim() : "";
    if (homeOutlet && String(outlet).trim().toLowerCase() !== homeOutlet.toLowerCase()) {
      notes += " [PENUGASAN SILANG OUTLET | Home: " + homeOutlet + " -> Scan: " + outlet + "] [AM Approval Required | Penugasan Silang Outlet]";
    }

    // Tagging Tugas Luar / Event - Membutuhkan SPV Approval
    var isTugasLuar = isTugasLuarMode || requestData.is_tugas_luar === true || String(requestData.is_tugas_luar).toLowerCase() === "true";
    var tugasLuarNotes = String(requestData.tugas_luar_notes || requestData.tugas_luar_reason || "").trim();
    if (isTugasLuar || tugasLuarNotes) {
      if (notes.indexOf("Supervisor Approval Required") === -1) {
        notes += " [TUGAS LUAR: " + (tugasLuarNotes || "Event") + "] [Supervisor Approval Required | Tugas Luar: " + (tugasLuarNotes || "Event") + "]";
      }
    }

    // Validasi Aturan Sequence & Duplikasi Absensi Hari Ini
    var todayRecords = getTodayAttendanceForNrp(attendanceSheet, nrp, formattedDate, ss.getSpreadsheetTimeZone());

    if (attendanceType === "CLOCK_IN") {
      if (todayRecords.hasClockIn) {
        return jsonResponse("error", "Anda tidak dapat melakukan Clock In berulang kali pada hari yang sama.", 400);
      }
    } else if (attendanceType === "START_BREAK") {
      if (!todayRecords.hasClockIn) {
        return jsonResponse("error", "Anda harus melakukan Clock In terlebih dahulu sebelum Start Break.", 400);
      }
      if (todayRecords.lastType === "START_BREAK") {
        return jsonResponse("error", "Anda tidak dapat melakukan Start Break berulang kali.", 400);
      }
      if (todayRecords.lastType === "CLOCK_OUT") {
        return jsonResponse("error", "Anda sudah melakukan Clock Out untuk hari ini.", 400);
      }
    } else if (attendanceType === "STOP_BREAK" || attendanceType === "END_BREAK") {
      if (todayRecords.lastType !== "START_BREAK") {
        return jsonResponse("error", "Stop Break hanya dapat dilakukan jika sebelumnya Anda telah melakukan Start Break.", 400);
      }
      if (todayRecords.lastType === "CLOCK_OUT") {
        return jsonResponse("error", "Anda sudah melakukan Clock Out untuk hari ini.", 400);
      }
    } else if (attendanceType === "CLOCK_OUT") {
      if (!todayRecords.hasClockIn) {
        return jsonResponse("error", "Anda harus melakukan Clock In terlebih dahulu sebelum Clock Out.", 400);
      }
      if (todayRecords.lastType === "START_BREAK") {
        return jsonResponse("error", "Anda sedang dalam masa Istirahat. Silakan lakukan Stop Break terlebih dahulu sebelum Clock Out.", 400);
      }
      if (todayRecords.lastType === "CLOCK_OUT") {
        return jsonResponse("error", "Anda sudah melakukan Clock Out untuk hari ini.", 400);
      }
    }

    var workingHourVal = requestData.working_hour || requestData.shift || "";
    if (workingHourVal) {
      notes += " [Jam Kerja: " + workingHourVal + "]";
    }

    // Cek Batas HK Normal Outlet & Tagging Supervisor Approval jika Melebihi HK
    var currentMonthStr = formattedDate.substring(0, 7); // "YYYY-MM"
    var schedSheet = ss.getSheetByName(TAB_OUTLET_SCHEDULE);
    var outletHK = getOutletHK(schedSheet, outlet);
    
    if (outletHK > 0) {
      var currentMonthlyDays = getMonthlyWorkingDaysForNrp(attendanceSheet, nrp, currentMonthStr, ss.getSpreadsheetTimeZone());
      var newMonthlyDays = todayRecords.hasClockIn ? currentMonthlyDays : (currentMonthlyDays + 1);
      
      if (newMonthlyDays > outletHK) {
        notes += " [Supervisor Approval Required | Exceeded Monthly HK (Day " + newMonthlyDays + "/" + outletHK + ")]";
      }
    }

    attendanceSheet.appendRow([
      nrp,
      employeeName,
      "'" + formattedTimestamp.replace('T', ' '),
      "'" + formattedDate,
      "'" + formattedTime,
      attendanceType,
      "WIB",
      outlet,
      notes,
      roundedDistance,
      workingHourVal,
      accuracyUser
    ]);
    
    // Lock TOTP agar tidak dapat di-replay oleh pengguna lain
    try {
      cache.put(cacheKey, "USED", 60);
    } catch (eCache) {}

    var successMsg = "Absensi berhasil dicatat untuk " + employeeName + ".";
    if (attendanceType === "CLOCK_IN") {
      successMsg = "Absensi Masuk Kerja berhasil! Selamat bertugas, " + employeeName + ". Semoga harimu menyenangkan! 🚀";
    } else if (attendanceType === "START_BREAK") {
      successMsg = "Absensi Mulai Istirahat berhasil! Selamat beristirahat, " + employeeName + ". Manfaatkan waktu Anda dengan baik! ☕";
    } else if (attendanceType === "STOP_BREAK" || attendanceType === "END_BREAK") {
      successMsg = "Absensi Selesai Istirahat berhasil! Selamat kembali bertugas, " + employeeName + ". Semangat melanjutkan aktivitas! 💼";
    } else if (attendanceType === "CLOCK_OUT") {
      successMsg = "Absensi Pulang Kerja berhasil! Terima kasih atas kerja keras Anda hari ini, " + employeeName + ". Hati-hati di jalan! 🏠✨";
    }

    return jsonResponse("success", successMsg, 200);
    
  } catch (error) {
    Logger.log("Error processing attendance: " + error.toString());
    return jsonResponse("error", "Terjadi kesalahan internal server: " + error.toString(), 500);
  } finally {
    try {
      lock.releaseLock();
    } catch (eLock) {}
  }
}

/**
 * Menyimpan Embedding Wajah & Device ID ke tab "Face_Embedding" (1 Device = 1 NRP Binding)
 */
function handleRegisterFace(requestData) {
  var nrp = requestData.nrp;
  var embedding = requestData.face_embedding; 
  var deviceId = requestData.device_id || "";
  
  if (!nrp || !embedding) {
    return jsonResponse("error", "NRP dan Data Wajah wajib disertakan.", 400);
  }
  
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  
  // 1. Validasi NRP di MP Database
  var employeeSheet = ss.getSheetByName(TAB_EMPLOYEES);
  if (!employeeSheet) {
    return jsonResponse("error", "Tab database master karyawan '" + TAB_EMPLOYEES + "' tidak ditemukan.", 500);
  }
  
  var employeeName = getEmployeeNameByNRP(employeeSheet, nrp);
  if (employeeName.indexOf("Unknown Staff") === 0) {
    return jsonResponse("error", "NRP " + nrp + " tidak terdaftar dalam database master karyawan.", 404);
  }
  
  // 2. Ambil atau buat tab "Face_Embedding" dengan kolom [NRP, Embedding, Device_ID, Registered_At]
  var faceSheet = ss.getSheetByName(TAB_FACE_EMBEDDING);
  if (!faceSheet) {
    faceSheet = ss.insertSheet(TAB_FACE_EMBEDDING);
    faceSheet.appendRow(["NRP", "Embedding", "Device_ID", "Registered_At"]);
  }
  
  var data = faceSheet.getDataRange().getValues();
  var headers = data.length > 0 ? data[0] : [];
  
  var embColIdx = 1;
  var devIdColIdx = -1;
  var dateColIdx = -1;

  for (var h = 0; h < headers.length; h++) {
    var hName = String(headers[h]).toLowerCase().trim();
    if (hName === "embedding" || hName === "face_embedding" || hName === "face embedding") embColIdx = h;
    if (hName === "device_id" || hName === "device id" || hName === "deviceid") devIdColIdx = h;
    if (hName === "registered_at" || hName === "registered at" || hName === "updated at" || hName === "updated_at" || hName === "date") dateColIdx = h;
  }

  // Jika kolom Device_ID tidak ada di header sheet, tambahkan header Device_ID otomatis!
  if (devIdColIdx === -1) {
    devIdColIdx = headers.length;
    faceSheet.getRange(1, devIdColIdx + 1).setValue("Device_ID");
  }
  if (dateColIdx === -1) {
    dateColIdx = headers.length + 1;
    faceSheet.getRange(1, dateColIdx + 1).setValue("Registered_At");
  }

  // 3. Cek apakah Device ID ini sudah dipakai oleh NRP LAIN
  var cleanTargetNrp = String(nrp || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();
  if (deviceId && devIdColIdx !== -1) {
    for (var i = 1; i < data.length; i++) {
      var existingNrp = String(data[i][0] || "").replace(/^'+/, '').replace(/^"+/, '').trim();
      var existingDevId = data[i].length > devIdColIdx ? String(data[i][devIdColIdx]).trim() : "";
      
      // Filter jika data di kolom devIdColIdx adalah format tanggal timestamp
      if (existingDevId && (existingDevId.indexOf("202") === 0 || existingDevId.match(/^\d{4}-\d{2}-\d{2}/))) {
        existingDevId = "";
      }

      if (existingDevId && existingDevId === deviceId && existingNrp.toLowerCase() !== cleanTargetNrp) {
        return jsonResponse("error", "Registrasi ditolak! Perangkat ini sudah terikat dengan NRP " + existingNrp + ". 1 HP hanya untuk 1 karyawan.", 403);
      }
    }
  }
  
  // 4. Cek apakah NRP ini sudah terdaftar sebelumnya
  var foundRow = -1;
  for (var i = 1; i < data.length; i++) {
    var rowNrpClean = String(data[i][0] || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();
    if (rowNrpClean === cleanTargetNrp) {
      foundRow = i + 1;
      var currentDevId = data[i].length > devIdColIdx ? String(data[i][devIdColIdx]).trim() : "";
      if (currentDevId && deviceId && currentDevId !== deviceId) {
        return jsonResponse("error", "NRP " + nrp + " sudah terdaftar di perangkat lain (" + currentDevId + "). Hubungi Admin/HRD untuk reset (Unbind Device).", 403);
      }
      break;
    }
  }
  
  var nowStr = Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), "yyyy-MM-dd HH:mm:ss");

  if (foundRow !== -1) {
    // Catat Device ID lama sebelum diupdate (untuk history)
    var oldDevId = data[foundRow - 1].length > devIdColIdx ? String(data[foundRow - 1][devIdColIdx]).trim() : "";
    var isDeviceChanged = oldDevId && deviceId && oldDevId !== deviceId;

    // Update embedding & Device ID
    faceSheet.getRange(foundRow, embColIdx + 1).setValue(JSON.stringify(embedding));
    if (deviceId) faceSheet.getRange(foundRow, devIdColIdx + 1).setValue(deviceId);
    faceSheet.getRange(foundRow, dateColIdx + 1).setValue(nowStr);

    // Log ke Device_Bind_History
    var updateNote = isDeviceChanged
      ? ("Re-bind dari device " + oldDevId + " ke device baru")
      : "Re-registrasi wajah (device sama)";
    logDeviceBindHistory(ss, nrp, deviceId || oldDevId, "BIND", "EMPLOYEE_SELF", updateNote);

    return jsonResponse("success", "Data registrasi wajah untuk NRP " + nrp + " telah berhasil diperbarui (updated)!", 200);
  } else {
    // Tambah baris baru [NRP, Embedding, Device_ID, Registered_At]
    // Ukuran array disesuaikan dengan indeks kolom tertinggi agar tidak ada data yang overwrite
    var maxColIdx = Math.max(0, embColIdx, devIdColIdx, dateColIdx);
    var newRow = [];
    for (var r = 0; r <= maxColIdx; r++) newRow.push("");
    newRow[0] = nrp;
    newRow[embColIdx] = JSON.stringify(embedding);
    newRow[devIdColIdx] = deviceId;
    newRow[dateColIdx] = nowStr;
    faceSheet.appendRow(newRow);

    // Log ke Device_Bind_History
    logDeviceBindHistory(ss, nrp, deviceId, "BIND", "EMPLOYEE_SELF", "Registrasi pertama kali");

    return jsonResponse("success", "Registrasi wajah & Perangkat (" + (deviceId || "Lokal") + ") untuk NRP " + nrp + " berhasil disimpan!", 200);
  }
}

/**
 * Mencatat setiap event Bind / Unbind Device ke tab "Device_Bind_History" untuk audit fraud.
 * Kolom: [Timestamp, NRP, Device_ID, Action, Triggered_By, Notes]
 */
function logDeviceBindHistory(ss, nrp, deviceId, action, triggeredBy, notes) {
  try {
    var TAB_HISTORY = "Device_Bind_History";
    var histSheet = ss.getSheetByName(TAB_HISTORY);
    if (!histSheet) {
      histSheet = ss.insertSheet(TAB_HISTORY);
      histSheet.appendRow(["Timestamp", "NRP", "Device_ID", "Action", "Triggered_By", "Notes"]);
      // Format header
      var headerRange = histSheet.getRange(1, 1, 1, 6);
      headerRange.setBackground("#1a1a2e");
      headerRange.setFontColor("#ffffff");
      headerRange.setFontWeight("bold");
      histSheet.setFrozenRows(1);
    }
    var nowStr = Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), "yyyy-MM-dd HH:mm:ss");
    histSheet.appendRow([
      nowStr,
      String(nrp || "").trim(),
      String(deviceId || "").trim(),
      String(action || "").trim(),       // "BIND" atau "UNBIND" atau "UNBIND_REQUEST"
      String(triggeredBy || "").trim(),  // "EMPLOYEE_SELF", "HR_ADMIN", "SYSTEM"
      String(notes || "").trim()
    ]);
  } catch (histErr) {
    Logger.log("[BindHistory] Gagal menulis history: " + histErr.toString());
  }
}

/**
 * Mengambil Embedding Wajah & Data Perangkat dari tab "Face_Embedding"
 */
function getFaceRecordByNRP(sheet, nrp) {
  var data = sheet.getDataRange().getValues();
  if (data.length < 2) return null;

  var embColIdx = 1;
  var devIdColIdx = -1; // Default -1 (hanya baca jika header Device_ID ditemukan)

  var headers = data[0];
  for (var h = 0; h < headers.length; h++) {
    var hName = String(headers[h]).toLowerCase().trim();
    if (hName === "device_id" || hName === "device id" || hName === "deviceid") devIdColIdx = h;
    if (hName === "embedding" || hName === "face_embedding" || hName === "face embedding") embColIdx = h;
  }

  var cleanTargetNrp = String(nrp || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();
  for (var i = 1; i < data.length; i++) {
    var cleanRowNrp = String(data[i][0] || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();
    if (cleanRowNrp === cleanTargetNrp) {
      var val = String(data[i][embColIdx]).trim();
      var devId = (devIdColIdx !== -1 && data[i].length > devIdColIdx) ? String(data[i][devIdColIdx]).trim() : "";
      
      // Jika devId bukan ID perangkat tetapi string tanggal/timestamp (misal "2026-08-06..."), abaikan
      if (devId && (devId.indexOf("202") === 0 || devId.match(/^\d{4}-\d{2}-\d{2}/))) {
        devId = "";
      }

      return {
        embedding: val ? JSON.parse(val) : null,
        deviceId: devId
      };
    }
  }
  return null;
}

function getFaceEmbeddingByNRP(sheet, nrp) {
  var rec = getFaceRecordByNRP(sheet, nrp);
  return rec ? rec.embedding : null;
}

/**
 * Mencatat Permintaan Unbind Device dari Karyawan ke tab "Device_Unbind_Requests"
 */
function handleRequestUnbindDevice(params) {
  var nrp = params.nrp || params.NRP;
  var reason = params.reason || params.notes || "Ganti HP Baru";
  var deviceId = params.device_id || params.deviceId || "";
  if (!nrp) {
    return jsonResponse("error", "NRP wajib diisi untuk mengajukan unbind device.", 400);
  }
  
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var TAB_UNBIND = "Device_Unbind_Requests";
  var unbindSheet = ss.getSheetByName(TAB_UNBIND);
  if (!unbindSheet) {
    unbindSheet = ss.insertSheet(TAB_UNBIND);
    unbindSheet.appendRow(["NRP", "Device_ID", "Reason", "Requested_At", "Status"]);
  }
  
  var nowStr = Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), "yyyy-MM-dd HH:mm:ss");
  unbindSheet.appendRow([String(nrp).trim(), String(deviceId).trim(), String(reason).trim(), nowStr, "PENDING"]);

  // Log permintaan unbind ke Device_Bind_History
  logDeviceBindHistory(ss, nrp, deviceId, "UNBIND_REQUEST", "EMPLOYEE_SELF",
    "Alasan: " + reason + " | Menunggu persetujuan HR");

  return jsonResponse("success", "Permintaan reset/unbind perangkat HP untuk NRP " + nrp + " berhasil dikirim ke HR Admin. Silakan konfirmasi ke HR Admin.", 200);
}

/**
 * Mengecek Status Permintaan Unbind Device untuk NRP tertentu.
 * Mengembalikan status terbaru: PENDING, APPROVED, REJECTED, atau NONE.
 */
function checkUnbindStatus(nrp) {
  if (!nrp) {
    return jsonResponse("error", "NRP wajib diisi.", 400);
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var TAB_UNBIND = "Device_Unbind_Requests";
  var unbindSheet = ss.getSheetByName(TAB_UNBIND);

  if (!unbindSheet) {
    // Tab belum ada → tidak ada permintaan sama sekali
    return jsonResponse("success", { status: "NONE", nrp: nrp }, 200);
  }

  var data = unbindSheet.getDataRange().getValues();
  if (data.length < 2) {
    return jsonResponse("success", { status: "NONE", nrp: nrp }, 200);
  }

  // Cari kolom NRP dan Status (fallback ke indeks default 0 & 4)
  var nrpCol = 0;
  var statusCol = 4;
  var requestedAtCol = 3;
  var headers = data[0];
  for (var h = 0; h < headers.length; h++) {
    var hName = String(headers[h]).toLowerCase().trim();
    if (hName === "nrp") nrpCol = h;
    if (hName === "status") statusCol = h;
    if (hName === "requested_at" || hName === "requested at") requestedAtCol = h;
  }

  // Cari semua baris untuk NRP ini, ambil yang terbaru (baris terakhir)
  var latestStatus = "NONE";
  var latestRequestedAt = "";
  var cleanNrp = String(nrp).trim().toLowerCase();

  for (var i = 1; i < data.length; i++) {
    var rowNrp = String(data[i][nrpCol]).trim().toLowerCase();
    if (rowNrp === cleanNrp) {
      var rowStatus = String(data[i][statusCol]).trim().toUpperCase();
      var rowDate = data[i][requestedAtCol];
      // Ambil baris terbaru berdasarkan urutan (data sudah ter-append, baris terbaru = terakhir)
      latestStatus = rowStatus || "PENDING";
      latestRequestedAt = rowDate ? String(rowDate) : "";
    }
  }

  return jsonResponse("success", {
    status: latestStatus,
    nrp: nrp,
    requested_at: latestRequestedAt
  }, 200);
}

/**
 * Mereset/Unbind Device ID untuk NRP pada tab "Face_Embedding" & memperbarui tab "Device_Unbind_Requests"
 */
function handleUnbindDevice(nrpToUnbind) {
  if (!nrpToUnbind) {
    return jsonResponse("error", "NRP wajib diisi.", 400);
  }
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var faceSheet = ss.getSheetByName(TAB_FACE_EMBEDDING);
  var clearedCount = 0;
  
  if (faceSheet) {
    var data = faceSheet.getDataRange().getValues();
    var headers = data.length > 0 ? data[0] : [];
    var devIdColIdx = 2;
    for (var h = 0; h < headers.length; h++) {
      var hName = String(headers[h]).toLowerCase().trim();
      if (hName === "device_id" || hName === "device id" || hName === "deviceid") devIdColIdx = h;
    }
    for (var i = 1; i < data.length; i++) {
      if (String(data[i][0]).trim().toLowerCase() === String(nrpToUnbind).trim().toLowerCase()) {
        var clearedDevId = data[i].length > devIdColIdx ? String(data[i][devIdColIdx]).trim() : "";
        faceSheet.getRange(i + 1, devIdColIdx + 1).setValue("");
        clearedCount++;
        // Log unbind event ke history
        logDeviceBindHistory(ss, nrpToUnbind, clearedDevId, "UNBIND", "HR_ADMIN", "Approved oleh HR Admin melalui portal");
      }
    }
  }
  
  var unbindSheet = ss.getSheetByName("Device_Unbind_Requests");
  if (unbindSheet) {
    var unbindData = unbindSheet.getDataRange().getValues();
    for (var u = 1; u < unbindData.length; u++) {
      if (String(unbindData[u][0]).trim().toLowerCase() === String(nrpToUnbind).trim().toLowerCase()) {
        unbindSheet.getRange(u + 1, 5).setValue("APPROVED");
      }
    }
  }
  
  return jsonResponse("success", "Device ID untuk NRP " + nrpToUnbind + " telah berhasil di-reset/unbind.", 200);
}

/**
 * Mencari konfigurasi Outlet berdasarkan Outlet (Nama Outlet)
 */
function getOutletConfig(sheet, outlet) {
  var data = getSheetValues_(sheet);
  if (data.length < 2) return null;

  var outletColIdx = 0;
  var latColIdx = 1;
  var lngColIdx = 2;
  var radiusColIdx = 3;
  var secretColIdx = 4;
  var pwaColIdx = -1;
  var headerFound = false;

  for (var r = 0; r < Math.min(data.length, 5); r++) {
    for (var h = 0; h < data[r].length; h++) {
      var hName = String(data[r][h]).toLowerCase().trim();
      if (hName === "outlet" || hName === "nama outlet" || hName === "outlet_name" || hName === "outlet_id" || hName === "cabang" || hName === "nama cabang") {
        outletColIdx = h;
        headerRowIdx = r;
        headerFound = true;
      }
      if (hName === "latitude" || hName === "lat") latColIdx = h;
      if (hName === "longitude" || hName === "lng" || hName === "long") lngColIdx = h;
      if (hName === "radius") radiusColIdx = h;
      if (hName === "secret" || hName === "secret_key" || hName === "secret key" || hName === "secretkey") secretColIdx = h;
      if (hName === "pwa_url" || hName === "pwa url" || hName === "pwaurl" || hName === "url pwa" || hName === "url_pwa") pwaColIdx = h;
    }
    if (headerFound) break;
  }

  var targetOutlet = String(outlet).trim().toLowerCase();
  for (var i = headerRowIdx + 1; i < data.length; i++) {
    var rowOutlet = String(data[i][outletColIdx]).trim().toLowerCase();
    if (rowOutlet === targetOutlet) {
      var parsedRadius = Number(data[i][radiusColIdx]);
      return {
        outlet: data[i][outletColIdx],
        latitude: Number(data[i][latColIdx]),
        longitude: Number(data[i][lngColIdx]),
        radius: (!isNaN(parsedRadius) && parsedRadius > 0) ? parsedRadius : 50,
        secret: String(data[i][secretColIdx]).trim(),
        pwa_url: (pwaColIdx !== -1 && data[i][pwaColIdx] && String(data[i][pwaColIdx]).trim() !== "") ? String(data[i][pwaColIdx]).trim() : "https://goldenlamian.dolanyu.com/"
      };
    }
  }
  return null;
}

/**
 * Mencari Nama Karyawan berdasarkan NRP (Mendukung Header di Row 1 or Row 2)
 */
function getEmployeeNameByNRP(sheet, nrp) {
  var data = getSheetValues_(sheet);
  var nrpColIndex = -1; 
  var nameColIndex = -1; 
  var headerRowIndex = -1;
  
  for (var r = 0; r < Math.min(data.length, 5); r++) {
    for (var c = 0; c < data[r].length; c++) {
      var val = String(data[r][c]).toUpperCase().trim();
      if (val === "NRP") {
        nrpColIndex = c;
        headerRowIndex = r;
      }
    }
    
    if (headerRowIndex !== -1) {
      for (var c = 0; c < data[r].length; c++) {
        var val = String(data[r][c]).toUpperCase().trim();
        if (val === "NAMA" || val === "NAMA STAFF" || val === "EMPLOYEE_NAME" || val === "NAMA LENGKAP") {
          nameColIndex = c;
        }
      }
      break;
    }
  }
  
  if (nrpColIndex === -1) nrpColIndex = 0;
  if (nameColIndex === -1) nameColIndex = 1;
  if (headerRowIndex === -1) headerRowIndex = 0;
  
  var cleanTarget = String(nrp || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();
  for (var i = headerRowIndex + 1; i < data.length; i++) {
    var cleanRowNrp = String(data[i][nrpColIndex] || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();
    if (cleanRowNrp === cleanTarget) {
      return String(data[i][nameColIndex]).trim();
    }
  }
  return "Unknown Staff (" + nrp + ")";
}

/**
 * Menghitung Euclidean Distance antara dua vektor embedding wajah (Server-side Face Matching)
 */
function calculateEuclideanDistance(vecA, vecB) {
  if (!vecA || !vecB || vecA.length === 0 || vecA.length !== vecB.length) return 999;
  var sum = 0;
  for (var i = 0; i < vecA.length; i++) {
    var diff = Number(vecA[i]) - Number(vecB[i]);
    sum += diff * diff;
  }
  return Math.sqrt(sum);
}

/**
 * Menghitung Jarak antara Dua Koordinat menggunakan Rumus Haversine (hasil dalam satuan Meter)
 */
function calculateDistance(lat1, lon1, lat2, lon2) {
  var R = 6371e3; 
  var phi1 = lat1 * Math.PI / 180;
  var phi2 = lat2 * Math.PI / 180;
  var deltaPhi = (lat2 - lat1) * Math.PI / 180;
  var deltaLambda = (lon2 - lon1) * Math.PI / 180;

  var a = Math.sin(deltaPhi / 2) * Math.sin(deltaPhi / 2) +
          Math.cos(phi1) * Math.cos(phi2) *
          Math.sin(deltaLambda / 2) * Math.sin(deltaLambda / 2);
  var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

  return R * c; 
}

/**
 * Memverifikasi Dynamic TOTP Token secara Serverless (Interval 10 Detik)
 */
function verifyTOTP(token, secret, timestamp) {
  var interval = 10;
  var counter = Math.floor(timestamp / interval);
  
  for (var offset = -1; offset <= 1; offset++) {
    var computedToken = generateTOTPForCounter(secret, counter + offset);
    if (computedToken === token) {
      return true;
    }
  }
  return false;
}

/**
 * Menghasilkan token TOTP untuk counter tertentu menggunakan HMAC-SHA1
 */
function generateTOTPForCounter(secret, counter) {
  try {
    var key = base32Decode(secret);
    
    var msg = [];
    var tempCounter = counter;
    for (var i = 7; i >= 0; i--) {
      msg[i] = tempCounter & 0xff;
      tempCounter = tempCounter >> 8;
    }
    
    var signatureBytes = Utilities.computeHmacSignature(
      Utilities.MacAlgorithm.HMAC_SHA_1,
      msg,
      key
    );
    
    var offset = signatureBytes[signatureBytes.length - 1] & 0xf;
    
    var binary = ((signatureBytes[offset] & 0x7f) << 24) |
                 ((signatureBytes[offset + 1] & 0xff) << 16) |
                 ((signatureBytes[offset + 2] & 0xff) << 8) |
                 (signatureBytes[offset + 3] & 0xff);
                 
    var otpVal = binary % 1000000;
    var otpStr = String(otpVal);
    
    while (otpStr.length < 6) {
      otpStr = "0" + otpStr;
    }
    return otpStr;
  } catch (err) {
    return "";
  }
}

/**
 * Decoder Base32 Sederhana karena Google Apps Script tidak memiliki decoder Base32 bawaan
 */
function base32Decode(base32) {
  var alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  base32 = base32.toUpperCase().replace(/=+$/, "");
  var length = base32.length;
  var bits = 0;
  var value = 0;
  var output = [];
  
  for (var i = 0; i < length; i++) {
    var val = alphabet.indexOf(base32.charAt(i));
    if (val === -1) throw new Error("Karakter Base32 tidak valid");
    value = (value << 5) | val;
    bits += 5;
    if (bits >= 8) {
      output.push((value >> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return output;
}

/**
 * Helper untuk Mengembalikan Respon HTTP dalam format JSON
 */
function jsonResponse(status, message, statusCode) {
  var responseObj = {
    status: status,
    message: message,
    code: statusCode
  };

  if (message && typeof message === 'object') {
    responseObj.data = message;
    if (message.found !== undefined) responseObj.found = message.found;
    if (message.nrp !== undefined) responseObj.nrp = message.nrp;
    if (message.name !== undefined) responseObj.name = message.name;
    if (message.outlet !== undefined) responseObj.outlet = message.outlet;
    if (message.position !== undefined) responseObj.position = message.position;
    if (message.is_supervisor !== undefined) responseObj.is_supervisor = message.is_supervisor;
    if (message.pending_requests !== undefined) responseObj.pending_requests = message.pending_requests;
  }
  
  return ContentService.createTextOutput(JSON.stringify(responseObj))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * FUNGSI UTILITAS: Menghasilkan Secret Key Base32 Acak
 */
function generateNewSecret() {
  var alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  var secret = "";
  for (var i = 0; i < 16; i++) {
    var randIndex = Math.floor(Math.random() * alphabet.length);
    secret += alphabet.charAt(randIndex);
  }
  Logger.log("🔑 SECRET KEY BASE32 BARU: " + secret);
  return secret;
}

/**
 * Helper untuk mendapatkan riwayat absensi NRP pada hari ini
 * Return: { hasClockIn: boolean, lastType: string|null, hasClockOut: boolean }
 */
function getTodayAttendanceForNrp(attendanceSheet, nrp, todayDateStr, tz) {
  var result = {
    hasClockIn: false,
    lastType: null,
    hasClockOut: false
  };

  if (!attendanceSheet) return result;

  var reader = getAttendanceReader_(attendanceSheet, tz);
  var picked = selectAttendanceRows_(reader, function (d) { return d === todayDateStr; }, [reader.idx.nrp, reader.idx.type]);
  var rows = picked.rows;
  var off = picked.offset;
  var idx = picked.idx;

  var targetNrpClean = String(nrp).trim().toLowerCase();

  // Pindai dari baris paling bawah ke atas untuk kecepatan maksimal
  for (var i = rows.length - 1; i >= 0; i--) {
    var rowNrp = String(rows[i][idx.nrp - off] || "").trim();
    if (rowNrp.toLowerCase() !== targetNrpClean) continue;

    var rowType = String(rows[i][idx.type - off] || "").trim();
    if (rowType === "CLOCK_IN") {
      result.hasClockIn = true;
    } else if (rowType === "CLOCK_OUT") {
      result.hasClockOut = true;
    }
    if (!result.lastType) {
      result.lastType = rowType;
    }
  }

  return result;
}

/**
 * Aksi Mendapatkan Daftar Shift untuk Outlet tertentu dari tab 'Outlet Schedule'
 */
function getOutletShifts(outletName) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var schedSheet = ss.getSheetByName(TAB_OUTLET_SCHEDULE);
  if (!schedSheet) {
    var sheets = ss.getSheets();
    for (var s = 0; s < sheets.length; s++) {
      var sName = sheets[s].getName().toLowerCase().trim();
      if (sName.indexOf("schedule") !== -1 || sName.indexOf("jadwal") !== -1 || sName.indexOf("shift") !== -1) {
        schedSheet = sheets[s];
        break;
      }
    }
  }

  if (!schedSheet) {
    return jsonResponse("success", [], 200);
  }

  var data = schedSheet.getDataRange().getValues();
  if (data.length <= 1) {
    return jsonResponse("success", [], 200);
  }

  var headers = data[0];
  var outletIdx = 0;
  var shiftIdx = 1;
  var workingHourIdx = 2;

  for (var h = 0; h < headers.length; h++) {
    var hName = String(headers[h]).toLowerCase().trim();
    if (hName.indexOf("outlet") !== -1) outletIdx = h;
    if (hName.indexOf("shift") !== -1 || hName.indexOf("jadwal") !== -1) shiftIdx = h;
    if (hName.indexOf("working") !== -1 || hName.indexOf("hour") !== -1 || hName.indexOf("jam") !== -1 || hName.indexOf("kerja") !== -1) workingHourIdx = h;
  }

  var cleanTarget = String(outletName || "").replace(/^'+/, '').replace(/^"+/, '').toLowerCase().trim();
  var shifts = [];

  for (var i = 1; i < data.length; i++) {
    var rawOutletVal = data[i][outletIdx];
    var cleanRow = String(rawOutletVal || "").replace(/^'+/, '').replace(/^"+/, '').toLowerCase().trim();

    if (cleanTarget && cleanRow) {
      var match = (cleanRow === cleanTarget) ||
                  (cleanRow.indexOf(cleanTarget) !== -1) ||
                  (cleanTarget.indexOf(cleanRow) !== -1);
      if (!match) continue;
    }

    var shiftName = String(data[i][shiftIdx] || "").trim();
    var rawWH = data[i][workingHourIdx];
    var workingHour = "";

    if (rawWH instanceof Date) {
      workingHour = Utilities.formatDate(rawWH, ss.getSpreadsheetTimeZone(), "HH:mm");
    } else {
      workingHour = String(rawWH || "").trim();
    }

    if (shiftName || workingHour) {
      shifts.push({
        shift: shiftName || "Shift",
        working_hour: workingHour || shiftName,
        label: shiftName ? (workingHour ? (shiftName + " (" + workingHour + ")") : shiftName) : workingHour
      });
    }
  }

  return jsonResponse("success", shifts, 200);
}

/**
 * Memeriksa apakah Karyawan berdasarkan NRP memiliki peran Supervisor
 * Menggunakan kolom 'Posisi Update' (atau 'Posisi') di tab MP Database
 */
function getEmployeeRoleByNRP(sheet, nrp) {
  if (!sheet) return { isSupervisor: false, isAreaManager: false, position: "", outlet: "", name: "", managedOutlets: [] };
  var data = getSheetValues_(sheet);
  if (data.length <= 1) return { isSupervisor: false, isAreaManager: false, position: "", outlet: "", name: "", managedOutlets: [] };

  var nrpIdx = -1;
  var posIdx = -1;
  var outletIdx = -1;
  var nameIdx = -1;
  var amIdx = -1;
  var headerRowIdx = -1;

  for (var r = 0; r < Math.min(data.length, 10); r++) {
    for (var c = 0; c < data[r].length; c++) {
      var val = String(data[r][c]).toUpperCase().trim();
      if (val === "NRP") {
        headerRowIdx = r;
        break;
      }
    }
    if (headerRowIdx !== -1) break;
  }

  if (headerRowIdx === -1) headerRowIdx = 0;

  var headerRow = data[headerRowIdx];
  for (var c = 0; c < headerRow.length; c++) {
    var val = String(headerRow[c]).toUpperCase().trim();

    if (val === "NRP") nrpIdx = c;
    
    if (val === "POSISI UPDATE") {
      posIdx = c;
    } else if (posIdx === -1 && (val.indexOf("POSISI") !== -1 || val.indexOf("POSITION") !== -1 || val.indexOf("JABATAN") !== -1 || val.indexOf("ROLE") !== -1)) {
      posIdx = c;
    }
    
    if (val === "OUTLET UPDATE" || val === "NAMA OUTLET" || val === "OUTLET" || val === "CABANG" || val === "PENEMPATAN" || val === "STORE" || val === "OUTLET_NAME") {
      if (outletIdx === -1 || val === "OUTLET UPDATE" || val === "NAMA OUTLET") outletIdx = c;
    } else if (outletIdx === -1 && val.indexOf("OUTLET") !== -1) {
      outletIdx = c;
    }
    
    if (val === "NAMA STAFF" || val === "NAMA" || val === "EMPLOYEE_NAME" || val === "NAMA LENGKAP" || val === "EMPLOYEE NAME") {
      if (nameIdx === -1 || val === "NAMA STAFF") nameIdx = c;
    }

    if (val === "AM BARU" || val === "AREA MANAGER" || val === "AM") {
      amIdx = c;
    }
  }

  if (nrpIdx === -1) nrpIdx = 0;

  var cleanNrpTarget = String(nrp || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();

  var foundName = "";
  var foundPos = "";
  var foundOutlet = "";

  for (var i = headerRowIdx + 1; i < data.length; i++) {
    var cleanRowNrp = String(data[i][nrpIdx] || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();
    if (cleanRowNrp === cleanNrpTarget) {
      foundName = (nameIdx !== -1 && nameIdx < data[i].length) ? String(data[i][nameIdx]).trim() : "";
      foundPos = (posIdx !== -1 && posIdx < data[i].length) ? String(data[i][posIdx]).trim() : "";
      foundOutlet = (outletIdx !== -1 && outletIdx < data[i].length) ? String(data[i][outletIdx]).trim() : "";
      break;
    }
  }

  if (!foundName && !foundPos && !foundOutlet) {
    return { isSupervisor: false, isAreaManager: false, position: "", outlet: "", name: "", managedOutlets: [] };
  }

  var cleanFoundName = foundName.toLowerCase();
  var posUpper = foundPos.toUpperCase();

  var isAM = posUpper.indexOf("AREA MANAGER") !== -1 ||
             posUpper === "AM" ||
             posUpper.indexOf("AM ") !== -1;

  var managedOutlets = [];

  if (amIdx !== -1) {
    for (var j = headerRowIdx + 1; j < data.length; j++) {
      var rowAmVal = String(data[j][amIdx] || "").replace(/^'+/, '').replace(/^"+/, '').trim().toLowerCase();
      var rowOutletVal = (outletIdx !== -1 && outletIdx < data[j].length) ? String(data[j][outletIdx]).trim() : "";
      if (rowAmVal && rowOutletVal) {
        if (rowAmVal === cleanFoundName || rowAmVal === cleanNrpTarget || (cleanFoundName && rowAmVal.indexOf(cleanFoundName) !== -1)) {
          isAM = true;
          if (managedOutlets.indexOf(rowOutletVal) === -1) {
            managedOutlets.push(rowOutletVal);
          }
        }
      }
    }
  }

  var isSvp = isAM ||
              posUpper === "SUPERVISOR" ||
              posUpper.indexOf("SUPERVISOR") !== -1 ||
              posUpper === "SVP" ||
              posUpper.indexOf("SVP") !== -1 ||
              posUpper === "SPV" ||
              posUpper.indexOf("MANAGER") !== -1 ||
              posUpper.indexOf("LEADER") !== -1 ||
              posUpper.indexOf("HEAD") !== -1;

  return {
    isSupervisor: isSvp,
    isAreaManager: isAM,
    position: isAM ? ("Area Manager (" + foundPos + ")") : foundPos,
    outlet: isAM && managedOutlets.length > 0 ? managedOutlets.join(", ") : foundOutlet,
    managedOutlets: managedOutlets,
    name: foundName
  };
}

/**
 * Aksi Mendapatkan Peran & Daftar Pengajuan Persetujuan untuk Supervisor / Area Manager
 */
function getSupervisorPendingInfo(nrp) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var empSheet = ss.getSheetByName(TAB_EMPLOYEES);
  
  var spvInfo = getEmployeeRoleByNRP(empSheet, nrp);
  if (!spvInfo.isSupervisor) {
    return jsonResponse("success", { is_supervisor: false, pending_requests: [] }, 200);
  }

  var attendanceSheet = ss.getSheetByName(TAB_ATTENDANCE);
  var pendingRequests = [];

  if (attendanceSheet) {
    var data = attendanceSheet.getDataRange().getValues();
    if (data.length > 1) {
      var headers = data[0];
      var nrpIdx = 0, nameIdx = 1, timestampIdx = 2, dateIdx = 3, timeIdx = 4, typeIdx = 5, outletIdx = 7, notesIdx = 8;
      
      for (var h = 0; h < headers.length; h++) {
        var hName = String(headers[h]).toLowerCase().trim();
        if (hName === "nrp") nrpIdx = h;
        if (hName === "employee_name") nameIdx = h;
        if (hName === "timestamp") timestampIdx = h;
        if (hName === "date") dateIdx = h;
        if (hName === "time") timeIdx = h;
        if (hName === "type") typeIdx = h;
        if (hName === "outlet" || hName === "outlet_id") outletIdx = h;
        if (hName === "notes" || hName === "catatan") notesIdx = h;
      }

      var managedSetLower = (spvInfo.managedOutlets || []).map(function(o) { return String(o).toLowerCase().trim(); });

      for (var i = 1; i < data.length; i++) {
        var rowNotes = String(data[i][notesIdx] || "");
        if (rowNotes.indexOf("Approval Required") !== -1 || rowNotes.indexOf("Supervisor Approval Required") !== -1 || rowNotes.indexOf("AM Approval Required") !== -1) {
          var rowOutlet = String(data[i][outletIdx] || "").trim();
          var cleanRowOutlet = rowOutlet.toLowerCase().trim();

          var isOutletMatch = false;

          if (spvInfo.isAreaManager && managedSetLower.length > 0) {
            for (var m = 0; m < managedSetLower.length; m++) {
              if (cleanRowOutlet === managedSetLower[m] || cleanRowOutlet.indexOf(managedSetLower[m]) !== -1 || managedSetLower[m].indexOf(cleanRowOutlet) !== -1) {
                isOutletMatch = true;
                break;
              }
            }
            if (!isOutletMatch) {
              var homeMatchRegex = rowNotes.match(/Home:\s*([^-\]\s|]+)/i);
              if (homeMatchRegex && homeMatchRegex[1]) {
                var cleanHomeOutlet = homeMatchRegex[1].toLowerCase().trim();
                for (var m = 0; m < managedSetLower.length; m++) {
                  if (cleanHomeOutlet === managedSetLower[m] || cleanHomeOutlet.indexOf(managedSetLower[m]) !== -1 || managedSetLower[m].indexOf(cleanHomeOutlet) !== -1) {
                    isOutletMatch = true;
                    break;
                  }
                }
              }
            }
          } else if (spvInfo.outlet && rowOutlet) {
            var cleanSpvOutlet = spvInfo.outlet.toLowerCase().trim();
            isOutletMatch = (cleanRowOutlet === cleanSpvOutlet || cleanRowOutlet.indexOf(cleanSpvOutlet) !== -1 || cleanSpvOutlet.indexOf(cleanRowOutlet) !== -1);
            
            if (!isOutletMatch) {
              var homeMatchRegex = rowNotes.match(/Home:\s*([^-\]\s|]+)/i);
              if (homeMatchRegex && homeMatchRegex[1]) {
                var cleanHomeOutlet = homeMatchRegex[1].toLowerCase().trim();
                if (cleanHomeOutlet === cleanSpvOutlet || cleanHomeOutlet.indexOf(cleanSpvOutlet) !== -1 || cleanSpvOutlet.indexOf(cleanHomeOutlet) !== -1) {
                  isOutletMatch = true;
                }
              }
            }
          } else {
            isOutletMatch = true;
          }

          if (!isOutletMatch) continue;

          var rawDate = data[i][dateIdx];
          var rawTime = data[i][timeIdx];
          var dateStr = (rawDate instanceof Date) ? Utilities.formatDate(rawDate, ss.getSpreadsheetTimeZone(), "yyyy-MM-dd") : String(rawDate || "").replace(/^'+/, '').substring(0, 10);
          var timeStr = (rawTime instanceof Date) ? Utilities.formatDate(rawTime, ss.getSpreadsheetTimeZone(), "HH:mm:ss") : String(rawTime || "").replace(/^'+/, '');

          var reasonMatch = rowNotes.match(/\|\s*([^\]]+)\]/);
          var reasonCategory = reasonMatch ? reasonMatch[1].trim() : "Perizinan Absen";

          pendingRequests.push({
            row_index: i + 1,
            nrp: String(data[i][nrpIdx]).trim(),
            employee_name: String(data[i][nameIdx]).trim(),
            timestamp: String(data[i][timestampIdx]).trim(),
            date: dateStr,
            time: timeStr,
            type: String(data[i][typeIdx]).trim(),
            outlet: rowOutlet,
            reason: reasonCategory,
            notes: rowNotes
          });
        }
      }
    }
  }

  return jsonResponse("success", {
    is_supervisor: true,
    is_area_manager: spvInfo.isAreaManager || false,
    supervisor_name: spvInfo.name || (spvInfo.isAreaManager ? "Area Manager" : "Supervisor"),
    supervisor_outlet: spvInfo.outlet || "",
    supervisor_position: spvInfo.position || "",
    pending_requests: pendingRequests
  }, 200);
}

/**
 * Aksi Menyetujui (Approve) atau Menolak (Reject) Pengajuan Absensi oleh Supervisor
 */
function handleSupervisorDecision(requestData) {
  var supervisorNrp = requestData.supervisor_nrp;
  var targetNrp = requestData.target_nrp;
  var targetTimestamp = requestData.target_timestamp;
  var decision = requestData.decision;

  if (!supervisorNrp || !targetNrp || !decision) {
    return jsonResponse("error", "Data parameter persetujuan tidak lengkap.", 400);
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var empSheet = ss.getSheetByName(TAB_EMPLOYEES);
  var spvInfo = getEmployeeRoleByNRP(empSheet, supervisorNrp);

  if (!spvInfo.isSupervisor) {
    return jsonResponse("error", "Akses ditolak: Anda tidak terdaftar sebagai Supervisor di MP Database.", 403);
  }

  var attendanceSheet = ss.getSheetByName(TAB_ATTENDANCE);
  if (!attendanceSheet) {
    return jsonResponse("error", "Tab 'attendance_records' tidak ditemukan.", 500);
  }

  var data = attendanceSheet.getDataRange().getValues();
  if (data.length <= 1) {
    return jsonResponse("error", "Data absensi kosong.", 404);
  }

  var headers = data[0];
  var nrpIdx = 0, timestampIdx = 2, notesIdx = 8;
  for (var h = 0; h < headers.length; h++) {
    var hName = String(headers[h]).toLowerCase().trim();
    if (hName === "nrp") nrpIdx = h;
    if (hName === "timestamp") timestampIdx = h;
    if (hName === "notes" || hName === "catatan") notesIdx = h;
  }

  var targetRowIdx = -1;
  for (var i = 1; i < data.length; i++) {
    var rowNrp = String(data[i][nrpIdx]).trim();
    var rowTs = String(data[i][timestampIdx]).trim();
    
    if (rowNrp.toLowerCase() === String(targetNrp).trim().toLowerCase()) {
      if (!targetTimestamp || rowTs.indexOf(String(targetTimestamp).trim()) !== -1 || String(targetTimestamp).trim().indexOf(rowTs) !== -1) {
        targetRowIdx = i + 1;
        break;
      }
    }
  }

  if (targetRowIdx === -1) {
    return jsonResponse("error", "Rekaman absensi staf tidak ditemukan.", 404);
  }

  var currentNotes = String(attendanceSheet.getRange(targetRowIdx, notesIdx + 1).getValue());
  var spvName = spvInfo.name || supervisorNrp;
  var nowStr = Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), "HH:mm");

  var tagStatus = decision === "APPROVED" 
    ? ("APPROVED by SPV " + spvName + " (" + supervisorNrp + ") at " + nowStr) 
    : ("REJECTED by SPV " + spvName + " (" + supervisorNrp + ") at " + nowStr);

  var newNotes = currentNotes.replace(/\[Supervisor Approval Required\s*\|\s*([^\]]+)\]/i, "[" + tagStatus + " | $1]");
  if (newNotes === currentNotes) {
    newNotes = currentNotes.replace(/\[(?:APPROVED|REJECTED) by SPV[^\]]*\|\s*([^\]]+)\]/i, "[" + tagStatus + " | $1]");
    if (newNotes === currentNotes) {
      newNotes = currentNotes + " [" + tagStatus + "]";
    }
  }

  attendanceSheet.getRange(targetRowIdx, notesIdx + 1).setValue(newNotes);

  return jsonResponse("success", "Pengajuan absensi berhasil di-" + (decision === "APPROVED" ? "setujui" : "tolak") + "!", 200);
}

/**
 * Helper untuk mendapatkan HK (Hari Kerja Normal) suatu Outlet dari tab 'Outlet Schedule'
 */
function getOutletHK(schedSheet, outletName) {
  if (!schedSheet || !outletName) return 0;
  var data = getSheetValues_(schedSheet);
  if (data.length <= 1) return 0;

  var headers = data[0];
  var outletIdx = 0;
  var hkIdx = -1;

  for (var h = 0; h < headers.length; h++) {
    var hName = String(headers[h]).toLowerCase().trim();
    if (hName.indexOf("outlet") !== -1 || hName.indexOf("cabang") !== -1) outletIdx = h;
    if (hName === "hk" || hName === "hari kerja" || hName.indexOf("hk") !== -1 || hName.indexOf("hari_kerja") !== -1) {
      hkIdx = h;
    }
  }

  if (hkIdx === -1) return 0;

  var cleanTarget = String(outletName).replace(/^'+/, '').replace(/^"+/, '').toLowerCase().trim();
  for (var i = 1; i < data.length; i++) {
    var rawOutletVal = data[i][outletIdx];
    var cleanRow = String(rawOutletVal || "").replace(/^'+/, '').replace(/^"+/, '').toLowerCase().trim();

    if (cleanTarget && cleanRow) {
      if (cleanRow === cleanTarget || cleanRow.indexOf(cleanTarget) !== -1 || cleanTarget.indexOf(cleanRow) !== -1) {
        var hkVal = Number(data[i][hkIdx]);
        if (!isNaN(hkVal) && hkVal > 0) {
          return hkVal;
        }
      }
    }
  }

  return 0;
}

/**
 * Helper untuk mendapatkan jumlah Hari Kerja (distinct date) yang telah dilakukan NRP pada bulan ini
 */
function getMonthlyWorkingDaysForNrp(attendanceSheet, nrp, targetMonthStr, tz) {
  if (!attendanceSheet || !nrp || !targetMonthStr) return 0;

  var reader = getAttendanceReader_(attendanceSheet, tz);
  var picked = selectAttendanceRows_(reader, function (d) { return !!d && d.substring(0, 7) === targetMonthStr; }, [reader.idx.nrp]);
  var rows = picked.rows;
  var off = picked.offset;
  var idx = picked.idx;

  var targetNrpClean = String(nrp).trim().toLowerCase();

  var distinctDates = {};
  for (var i = 0; i < rows.length; i++) {
    var rowNrp = String(rows[i][idx.nrp - off]).trim();
    if (rowNrp.toLowerCase() !== targetNrpClean) continue;
    distinctDates[reader.normalizeDate(rows[i][idx.date - off])] = true;
  }

  return Object.keys(distinctDates).length;
}

/**
 * Mencari Data Pengguna (NRP, Nama, Outlet, Jabatan, Peran Supervisor) berdasarkan Device ID
 */
function getUserByDeviceId(deviceId, optionalNrp) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var faceSheet = ss.getSheetByName(TAB_FACE_EMBEDDING);
  var empSheet = ss.getSheetByName(TAB_EMPLOYEES);
  
  var targetNrp = optionalNrp || "";

  if (!targetNrp && faceSheet && deviceId) {
    var faceData = faceSheet.getDataRange().getValues();
    if (faceData.length > 1) {
      var headers = faceData[0];
      var nrpIdx = 0;
      var devIdIdx = 2;
      for (var h = 0; h < headers.length; h++) {
        var hName = String(headers[h]).toLowerCase().trim();
        if (hName === "nrp") nrpIdx = h;
        if (hName === "device_id" || hName === "device id" || hName === "deviceid") devIdIdx = h;
      }
      
      var cleanDevId = String(deviceId).trim().toLowerCase();
      for (var i = 1; i < faceData.length; i++) {
        var rowDevId = String(faceData[i][devIdIdx] || "").trim().toLowerCase();
        if (rowDevId && rowDevId === cleanDevId) {
          targetNrp = String(faceData[i][nrpIdx] || "").replace(/^'+/, '').replace(/^"+/, '').trim();
          break;
        }
      }
    }
  }

  if (!targetNrp) {
    return jsonResponse("success", { found: false, message: "Device ID belum terdaftar di cloud" }, 200);
  }

  var spvInfo = getEmployeeRoleByNRP(empSheet, targetNrp);
  var attSheet = ss.getSheetByName(TAB_ATTENDANCE);
  var tz = ss.getSpreadsheetTimeZone();
  var todayDateStr = Utilities.formatDate(new Date(), tz, "yyyy-MM-dd");
  var todayStatus = attSheet ? getTodayAttendanceForNrp(attSheet, targetNrp, todayDateStr, tz) : { hasClockIn: false, hasClockOut: false, lastType: "" };
  
  return jsonResponse("success", {
    found: true,
    nrp: targetNrp,
    name: spvInfo.name || targetNrp,
    outlet: spvInfo.outlet || "",
    position: spvInfo.position || "",
    is_supervisor: spvInfo.isSupervisor,
    today_status: {
      has_clock_in: todayStatus.hasClockIn,
      has_clock_out: todayStatus.hasClockOut,
      last_type: todayStatus.lastType
    }
  }, 200);
}

/**
 * Mendapatkan daftar seluruh Area Manager dari tab 'AM Baru'
 */
function getAreaManagerList() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("AM Baru");
  if (!sheet) {
    var mpSheet = ss.getSheetByName(TAB_EMPLOYEES);
    if (!mpSheet) return jsonResponse("success", [], 200);
    var mpData = mpSheet.getDataRange().getValues();
    if (mpData.length <= 1) return jsonResponse("success", [], 200);

    var amColIdx = -1;
    for (var c = 0; c < mpData[0].length; c++) {
      var h = String(mpData[0][c]).toUpperCase().trim();
      if (h === "AM BARU" || h === "AREA MANAGER" || h === "AM") {
        amColIdx = c;
        break;
      }
    }
    if (amColIdx === -1) return jsonResponse("success", [], 200);

    var list = [];
    for (var i = 1; i < mpData.length; i++) {
      var val = String(mpData[i][amColIdx] || "").trim();
      if (val && list.indexOf(val) === -1 && val.toLowerCase() !== "nan" && val.toLowerCase() !== "none") {
        list.push(val);
      }
    }
    return jsonResponse("success", list.sort(), 200);
  }

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return jsonResponse("success", [], 200);

  var amList = [];
  var headerRow = data[0];
  var amIdx = 0;
  for (var c = 0; c < headerRow.length; c++) {
    if (String(headerRow[c]).toUpperCase().trim().indexOf("AM") !== -1) {
      amIdx = c;
      break;
    }
  }

  for (var i = 1; i < data.length; i++) {
    var name = String(data[i][amIdx] || "").trim();
    if (name && amList.indexOf(name) === -1 && name.toLowerCase() !== "nan" && name.toLowerCase() !== "none") {
      amList.push(name);
    }
  }

  return jsonResponse("success", amList.sort(), 200);
}

/**
 * Memverifikasi Login Area Manager menggunakan Nama & PIN
 */
function handleAreaManagerLogin(amName, pin) {
  if (!amName || !pin) {
    return jsonResponse("error", "Nama Area Manager dan PIN wajib diisi.", 400);
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("AM Baru");
  var targetPin = "1234";
  var foundAM = false;

  var cleanAmTarget = String(amName).trim().toLowerCase();
  var cleanPinReq = String(pin).trim();

  if (sheet) {
    var data = sheet.getDataRange().getValues();
    if (data.length > 1) {
      var headerRow = data[0];
      var amIdx = 0;
      var pinIdx = 1;
      for (var c = 0; c < headerRow.length; c++) {
        var h = String(headerRow[c]).toUpperCase().trim();
        if (h.indexOf("AM") !== -1) amIdx = c;
        if (h === "PIN" || h === "PASSCODE" || h === "PASSWORD") pinIdx = c;
      }

      for (var i = 1; i < data.length; i++) {
        var nameVal = String(data[i][amIdx] || "").trim();
        if (nameVal.toLowerCase() === cleanAmTarget || (cleanAmTarget && nameVal.toLowerCase().indexOf(cleanAmTarget) !== -1)) {
          foundAM = true;
          var storedPin = (pinIdx < data[i].length && data[i][pinIdx] !== undefined) ? String(data[i][pinIdx]).trim() : "1234";
          targetPin = storedPin || "1234";
          break;
        }
      }
    }
  } else {
    var mpSheet = ss.getSheetByName(TAB_EMPLOYEES);
    if (mpSheet) {
      var mpData = mpSheet.getDataRange().getValues();
      if (mpData.length > 1) {
        for (var i = 1; i < mpData.length; i++) {
          var rowStr = mpData[i].join(" ").toLowerCase();
          if (rowStr.indexOf(cleanAmTarget) !== -1) {
            foundAM = true;
            break;
          }
        }
      }
    }
  }

  if (!foundAM) {
    return jsonResponse("error", "Nama Area Manager '" + amName + "' tidak terdaftar di database.", 404);
  }

  if (cleanPinReq !== targetPin) {
    return jsonResponse("error", "PIN / Passcode Area Manager salah.", 401);
  }

  var empSheet = ss.getSheetByName(TAB_EMPLOYEES);
  var roleInfo = getEmployeeRoleByNRP(empSheet, amName);
  var pendingRes = getSupervisorPendingInfo(amName);

  var pendingData = [];
  try {
    var parsed = JSON.parse(pendingRes.getContent());
    pendingData = (parsed && parsed.data && parsed.data.pending_requests) ? parsed.data.pending_requests : [];
  } catch (e) { }

  return jsonResponse("success", {
    is_area_manager: true,
    is_supervisor: true,
    supervisor_name: amName,
    supervisor_outlet: roleInfo.outlet || "Semua Area AM",
    supervisor_position: "Area Manager",
    is_default_pin: (targetPin === "1234"),
    pending_requests: pendingData
  }, 200);
}

/**
 * Mengubah PIN Area Manager di tab 'AM Baru'
 */
function handleChangeAmPin(amName, oldPin, newPin) {
  if (!amName || !oldPin || !newPin) {
    return jsonResponse("error", "Nama AM, PIN Lama, dan PIN Baru wajib diisi.", 400);
  }
  if (String(newPin).trim().length < 4) {
    return jsonResponse("error", "PIN Baru minimal 4 digit.", 400);
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("AM Baru");
  if (!sheet) {
    return jsonResponse("error", "Tab 'AM Baru' tidak ditemukan di Google Sheets.", 404);
  }

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) {
    return jsonResponse("error", "Data AM tidak ditemukan.", 404);
  }

  var headerRow = data[0];
  var amIdx = 0;
  var pinIdx = 1;
  for (var c = 0; c < headerRow.length; c++) {
    var h = String(headerRow[c]).toUpperCase().trim();
    if (h.indexOf("AM") !== -1) amIdx = c;
    if (h === "PIN" || h === "PASSCODE" || h === "PASSWORD") pinIdx = c;
  }

  var cleanTarget = String(amName).trim().toLowerCase();
  var foundRow = -1;
  var currentPin = "1234";

  for (var i = 1; i < data.length; i++) {
    var nameVal = String(data[i][amIdx] || "").trim();
    if (nameVal.toLowerCase() === cleanTarget || (cleanTarget && nameVal.toLowerCase().indexOf(cleanTarget) !== -1)) {
      foundRow = i + 1;
      currentPin = (pinIdx < data[i].length && data[i][pinIdx] !== undefined) ? String(data[i][pinIdx]).trim() : "1234";
      break;
    }
  }

  if (foundRow === -1) {
    return jsonResponse("error", "Nama Area Manager tidak terdaftar.", 404);
  }

  if (String(oldPin).trim() !== currentPin) {
    return jsonResponse("error", "PIN Lama yang Anda masukkan salah.", 401);
  }

  sheet.getRange(foundRow, pinIdx + 1).setValue(String(newPin).trim());
  return jsonResponse("success", "PIN Area Manager berhasil diperbarui!", 200);
}
