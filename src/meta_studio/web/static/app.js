(() => {
  "use strict";

  const state = {
    profiles: [],
    activeProfileId: null,
    activeCopyrightTemplate: "",
    genresCatalog: { distributor_primary: [], tags: [] },
    selectedGenres: new Set(),
    coverFile: null,
    coverPreviewUrl: null,
    tracks: [], // { id, file, filename, format, title, artist, discNumber, trackNumber, isrc }
    artistTouched: false,
    lastExportedDir: null,
    previewTimer: null,
    previewSeq: 0,
    draggedTrackId: null,
  };

  const els = {
    ffmpegBadge: document.getElementById("ffmpeg-badge"),
    profilesBar: document.getElementById("profiles-bar"),
    btnSaveProfile: document.getElementById("btn-save-profile"),
    profileDialog: document.getElementById("profile-dialog"),
    profileForm: document.getElementById("profile-form"),
    newProfileName: document.getElementById("new-profile-name"),
    newProfileCatalogPrefix: document.getElementById("new-profile-catalog-prefix"),
    btnCancelProfile: document.getElementById("btn-cancel-profile"),

    coverDropzone: document.getElementById("cover-dropzone"),
    coverInput: document.getElementById("cover-input"),
    coverPreview: document.getElementById("cover-preview"),
    coverPlaceholder: document.getElementById("cover-placeholder"),
    btnRemoveCover: document.getElementById("btn-remove-cover"),
    coverMeta: document.getElementById("cover-meta"),

    albumTitle: document.getElementById("album-title"),
    albumArtist: document.getElementById("album-artist"),
    releaseArtist: document.getElementById("release-artist"),
    releaseDate: document.getElementById("release-date"),
    btnToday: document.getElementById("btn-today"),
    originalDate: document.getElementById("original-date"),
    releaseType: document.getElementById("release-type"),
    releaseStatus: document.getElementById("release-status"),
    releaseLabel: document.getElementById("release-label"),
    catalogNumber: document.getElementById("catalog-number"),
    releaseCopyright: document.getElementById("release-copyright"),

    genreChips: document.getElementById("genre-chips"),
    selectedGenresCount: document.getElementById("selected-genres-count"),
    newGenreInput: document.getElementById("new-genre-input"),
    btnAddGenre: document.getElementById("btn-add-genre"),

    outputDir: document.getElementById("output-dir"),
    btnPickFolder: document.getElementById("btn-pick-folder"),

    distributionAccordion: document.getElementById("distribution-accordion"),
    distPrimaryGenre: document.getElementById("dist-primary-genre"),
    distSecondaryGenre: document.getElementById("dist-secondary-genre"),
    distLanguage: document.getElementById("dist-language"),
    distUpc: document.getElementById("dist-upc"),
    distInstrumental: document.getElementById("dist-instrumental"),
    distExplicit: document.getElementById("dist-explicit"),
    distCredits: document.getElementById("dist-credits"),

    tracksCount: document.getElementById("tracks-count"),
    btnCleanAll: document.getElementById("btn-clean-all"),
    btnClearTracks: document.getElementById("btn-clear-tracks"),
    audioDropzone: document.getElementById("audio-dropzone"),
    audioInput: document.getElementById("audio-input"),
    tracksTableWrap: document.getElementById("tracks-table-wrap"),
    tracksTbody: document.getElementById("tracks-tbody"),

    previewFolder: document.getElementById("preview-folder"),
    previewFiles: document.getElementById("preview-files"),

    verificationCard: document.getElementById("verification-card"),
    verificationBadge: document.getElementById("verification-badge"),
    verificationHeading: document.getElementById("verification-heading"),
    verificationPath: document.getElementById("verification-path"),
    verificationList: document.getElementById("verification-list"),
    btnOpenFinderCard: document.getElementById("btn-open-finder-card"),

    importInfoInput: document.getElementById("import-info-input"),
    btnImportInfo: document.getElementById("btn-import-info"),
    statusMessage: document.getElementById("status-message"),
    btnOpenFinderBar: document.getElementById("btn-open-finder-bar"),
    btnProcess: document.getElementById("btn-process"),
  };

  function todayIso() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function currentYear() {
    const dateVal = els.releaseDate.value.trim();
    if (/^\d{4}/.test(dateVal)) return dateVal.slice(0, 4);
    return String(new Date().getFullYear());
  }

  function setStatus(text, kind = "neutral") {
    els.statusMessage.textContent = text;
    els.statusMessage.classList.remove("is-error", "is-ok");
    if (kind === "error") els.statusMessage.classList.add("is-error");
    if (kind === "ok") els.statusMessage.classList.add("is-ok");
  }

  function selectedWavConversion() {
    const checked = document.querySelector('input[name="wav-conversion"]:checked');
    return checked ? checked.value : "flac";
  }

  function setWavConversion(mode) {
    const radio = document.querySelector(`input[name="wav-conversion"][value="${mode}"]`);
    if (radio) radio.checked = true;
  }

  async function apiJson(url, options = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Error HTTP ${res.status}`);
    }
    return data;
  }

  // --- Profiles ---
  function renderProfiles() {
    els.profilesBar.innerHTML = "";
    for (const profile of state.profiles) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "profile-btn" + (state.activeProfileId === profile.id ? " active" : "");
      btn.textContent = profile.name;
      btn.addEventListener("click", () => applyProfile(profile));
      els.profilesBar.appendChild(btn);
    }
  }

  function applyProfile(profile) {
    state.activeProfileId = profile.id;
    state.activeCopyrightTemplate = profile.copyright || "";

    els.albumArtist.value = profile.album_artist || "";
    els.releaseArtist.value = profile.artist || profile.album_artist || "";
    state.artistTouched = false;
    els.releaseLabel.value = profile.label || "";

    const year = currentYear();
    els.releaseCopyright.value = profile.copyright
      ? profile.copyright.replaceAll("{year}", year)
      : "";

    if (profile.release_type) {
      els.releaseType.value = profile.release_type;
      els.releaseStatus.value = profile.release_type === "demo" ? "Demo" : "Final";
    }

    const prefix = profile.catalog_prefix || "";
    if (prefix) {
      const currentCat = els.catalogNumber.value.trim();
      if (!currentCat || !currentCat.startsWith(prefix)) {
        els.catalogNumber.value = prefix;
      }
    } else if (profile.release_type === "demo") {
      els.catalogNumber.value = "";
    }

    // Actualizar artista en pistas que todavía no tenían artista propio
    for (const track of state.tracks) {
      if (!track.artist) {
        track.artist = els.releaseArtist.value.trim();
      }
    }

    renderProfiles();
    renderTracks();
    schedulePreview();
    setStatus(`Perfil aplicado: ${profile.name}`);
  }

  // --- Genres ---
  function renderGenres() {
    els.genreChips.innerHTML = "";
    const allTags = [...state.genresCatalog.tags];
    for (const g of state.selectedGenres) {
      if (!allTags.includes(g)) allTags.push(g);
    }

    for (const tag of allTags) {
      const btn = document.createElement("button");
      btn.type = "button";
      const isSelected = state.selectedGenres.has(tag);
      btn.className = "chip" + (isSelected ? " selected" : "");
      btn.textContent = tag;
      btn.setAttribute("aria-pressed", String(isSelected));
      btn.addEventListener("click", () => {
        if (state.selectedGenres.has(tag)) {
          state.selectedGenres.delete(tag);
        } else {
          state.selectedGenres.add(tag);
        }
        renderGenres();
      });
      els.genreChips.appendChild(btn);
    }

    const count = state.selectedGenres.size;
    els.selectedGenresCount.textContent =
      count === 1 ? "1 seleccionado" : `${count} seleccionados`;

    // Poblar selects de género primario/secundario de distribuidora
    const populateSelect = (selectEl) => {
      const current = selectEl.value;
      selectEl.innerHTML = '<option value="">— Sin definir —</option>';
      for (const item of state.genresCatalog.distributor_primary) {
        const opt = document.createElement("option");
        opt.value = item;
        opt.textContent = item;
        selectEl.appendChild(opt);
      }
      if (current) selectEl.value = current;
    };
    populateSelect(els.distPrimaryGenre);
    populateSelect(els.distSecondaryGenre);
  }

  async function addNewGenre() {
    const raw = els.newGenreInput.value.trim();
    if (!raw) return;

    state.selectedGenres.add(raw);
    els.newGenreInput.value = "";

    if (!state.genresCatalog.tags.includes(raw)) {
      const updated = {
        distributor_primary: state.genresCatalog.distributor_primary,
        tags: [...state.genresCatalog.tags, raw],
      };
      try {
        state.genresCatalog = await apiJson("/api/presets/genres", {
          method: "PUT",
          body: JSON.stringify(updated),
        });
      } catch (err) {
        setStatus(`Género añadido solo en esta sesión: ${err.message}`, "error");
      }
    }
    renderGenres();
  }

  // --- Cover ---
  function setCoverFile(file) {
    if (!file) return;
    if (state.coverPreviewUrl) URL.revokeObjectURL(state.coverPreviewUrl);
    state.coverFile = file;
    const url = URL.createObjectURL(file);
    state.coverPreviewUrl = url;

    els.coverPreview.src = url;
    els.coverPreview.classList.remove("hidden");
    els.coverPlaceholder.classList.add("hidden");
    els.btnRemoveCover.classList.remove("hidden");

    const img = new Image();
    img.onload = () => {
      const targetDim = Math.min(Math.max(img.naturalWidth, img.naturalHeight), 1400);
      const resizedNote =
        img.naturalWidth > 1400 || img.naturalHeight > 1400
          ? ` → máx. ${targetDim} px JPEG`
          : " · JPEG";
      els.coverMeta.textContent = `${file.name} (${img.naturalWidth} × ${img.naturalHeight} px${resizedNote})`;
    };
    img.src = url;
    schedulePreview();
  }

  function clearCoverFile() {
    if (state.coverPreviewUrl) URL.revokeObjectURL(state.coverPreviewUrl);
    state.coverFile = null;
    state.coverPreviewUrl = null;
    els.coverInput.value = "";
    els.coverPreview.src = "";
    els.coverPreview.classList.add("hidden");
    els.coverPlaceholder.classList.remove("hidden");
    els.btnRemoveCover.classList.add("hidden");
    els.coverMeta.textContent = "Sin portada seleccionada (se guardará cover.jpg ≤ 1400 px al elegir una).";
    schedulePreview();
  }

  // --- Tracks ---
  function recalculateTrackNumbers() {
    const perDiscCounter = new Map();
    for (const track of state.tracks) {
      const disc = Math.max(1, Number(track.discNumber) || 1);
      track.discNumber = disc;
      const nextNum = (perDiscCounter.get(disc) || 0) + 1;
      perDiscCounter.set(disc, nextNum);
      track.trackNumber = nextNum;
    }
  }

  function detectExt(filename) {
    const idx = filename.lastIndexOf(".");
    return idx >= 0 ? filename.slice(idx + 1).toLowerCase() : "";
  }

  async function addAudioFiles(fileList) {
    const valid = Array.from(fileList).filter((file) =>
      ["wav", "flac", "mp3"].includes(detectExt(file.name))
    );
    if (valid.length === 0) {
      setStatus("Solo se aceptan archivos WAV, FLAC o MP3.", "error");
      return;
    }

    // Orden natural inicial por nombre de archivo
    valid.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));

    let cleanedTitles = valid.map((f) => f.name.replace(/\.[^.]+$/, ""));
    try {
      const data = await apiJson("/api/clean-names", {
        method: "POST",
        body: JSON.stringify({ filenames: valid.map((f) => f.name) }),
      });
      if (Array.isArray(data.cleaned)) cleanedTitles = data.cleaned;
    } catch {
      // Fallback silencioso
    }

    const defaultArtist = els.releaseArtist.value.trim() || els.albumArtist.value.trim();
    valid.forEach((file, idx) => {
      state.tracks.push({
        id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        file,
        filename: file.name,
        format: detectExt(file.name),
        title: cleanedTitles[idx] || file.name,
        artist: defaultArtist,
        discNumber: 1,
        trackNumber: 1,
        isrc: "",
      });
    });

    recalculateTrackNumbers();
    renderTracks();
    schedulePreview();
    setStatus(`${valid.length} archivo(s) añadidos con nombres limpios.`);
  }

  function syncIsrcVisibility() {
    const showIsrc = els.distributionAccordion.open;
    document.querySelectorAll(".isrc-col").forEach((el) => {
      el.classList.toggle("hidden", !showIsrc);
    });
  }

  function renderTracks() {
    recalculateTrackNumbers();
    const count = state.tracks.length;
    els.tracksCount.textContent = count === 1 ? "1 pista" : `${count} pistas`;
    els.btnCleanAll.disabled = count === 0;
    els.btnClearTracks.disabled = count === 0;
    els.tracksTableWrap.classList.toggle("hidden", count === 0);

    els.tracksTbody.innerHTML = "";
    const showIsrc = els.distributionAccordion.open;

    state.tracks.forEach((track) => {
      const tr = document.createElement("tr");
      tr.className = "track-row";
      tr.draggable = true;
      tr.dataset.trackId = track.id;

      // Drag & Drop de filas
      tr.addEventListener("dragstart", (e) => {
        state.draggedTrackId = track.id;
        tr.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", track.id);
      });

      tr.addEventListener("dragend", () => {
        state.draggedTrackId = null;
        tr.classList.remove("dragging");
        document
          .querySelectorAll(".track-row")
          .forEach((row) => row.classList.remove("drag-target-top", "drag-target-bottom"));
      });

      tr.addEventListener("dragover", (e) => {
        if (!state.draggedTrackId || state.draggedTrackId === track.id) return;
        e.preventDefault();
        const rect = tr.getBoundingClientRect();
        const isTop = e.clientY < rect.top + rect.height / 2;
        tr.classList.toggle("drag-target-top", isTop);
        tr.classList.toggle("drag-target-bottom", !isTop);
      });

      tr.addEventListener("dragleave", () => {
        tr.classList.remove("drag-target-top", "drag-target-bottom");
      });

      tr.addEventListener("drop", (e) => {
        e.preventDefault();
        const draggedId = state.draggedTrackId;
        if (!draggedId || draggedId === track.id) return;

        const fromIdx = state.tracks.findIndex((t) => t.id === draggedId);
        let toIdx = state.tracks.findIndex((t) => t.id === track.id);
        if (fromIdx < 0 || toIdx < 0) return;

        const rect = tr.getBoundingClientRect();
        const insertAfter = e.clientY >= rect.top + rect.height / 2;

        const [moved] = state.tracks.splice(fromIdx, 1);
        toIdx = state.tracks.findIndex((t) => t.id === track.id);
        state.tracks.splice(insertAfter ? toIdx + 1 : toIdx, 0, moved);

        recalculateTrackNumbers();
        renderTracks();
        schedulePreview();
      });

      // Handle
      const tdDrag = document.createElement("td");
      tdDrag.className = "col-drag";
      tdDrag.innerHTML = '<span class="drag-handle" title="Arrastra para reordenar">⋮⋮</span>';
      tr.appendChild(tdDrag);

      // Track number
      const tdNum = document.createElement("td");
      tdNum.className = "col-num";
      tdNum.textContent = String(track.trackNumber).padStart(2, "0");
      tr.appendChild(tdNum);

      // Disc
      const tdDisc = document.createElement("td");
      tdDisc.className = "col-disc";
      const discInput = document.createElement("input");
      discInput.type = "number";
      discInput.min = "1";
      discInput.max = "99";
      discInput.value = String(track.discNumber);
      discInput.addEventListener("change", () => {
        track.discNumber = Math.max(1, parseInt(discInput.value, 10) || 1);
        renderTracks();
        schedulePreview();
      });
      tdDisc.appendChild(discInput);
      tr.appendChild(tdDisc);

      // Title
      const tdTitle = document.createElement("td");
      const titleInput = document.createElement("input");
      titleInput.type = "text";
      titleInput.value = track.title;
      titleInput.placeholder = "Título de la canción";
      titleInput.addEventListener("input", () => {
        track.title = titleInput.value;
        schedulePreview();
      });
      tdTitle.appendChild(titleInput);
      tr.appendChild(tdTitle);

      // Artist
      const tdArtist = document.createElement("td");
      const artistInput = document.createElement("input");
      artistInput.type = "text";
      artistInput.value = track.artist;
      artistInput.placeholder = els.releaseArtist.value.trim() || "Artista";
      artistInput.addEventListener("input", () => {
        track.artist = artistInput.value;
      });
      tdArtist.appendChild(artistInput);
      tr.appendChild(tdArtist);

      // ISRC (visible when distribution accordion is open)
      const tdIsrc = document.createElement("td");
      tdIsrc.className = "col-isrc isrc-col" + (showIsrc ? "" : " hidden");
      const isrcInput = document.createElement("input");
      isrcInput.type = "text";
      isrcInput.value = track.isrc;
      isrcInput.placeholder = "ISRC";
      isrcInput.addEventListener("input", () => {
        track.isrc = isrcInput.value;
      });
      tdIsrc.appendChild(isrcInput);
      tr.appendChild(tdIsrc);

      // Format badge
      const tdFmt = document.createElement("td");
      tdFmt.className = "col-fmt";
      tdFmt.innerHTML = `<span class="fmt-badge">${track.format}</span>`;
      tr.appendChild(tdFmt);

      // Delete
      const tdDel = document.createElement("td");
      tdDel.className = "col-del";
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn-icon-del";
      delBtn.title = "Quitar pista";
      delBtn.textContent = "×";
      delBtn.addEventListener("click", () => {
        state.tracks = state.tracks.filter((t) => t.id !== track.id);
        renderTracks();
        schedulePreview();
      });
      tdDel.appendChild(delBtn);
      tr.appendChild(tdDel);

      els.tracksTbody.appendChild(tr);
    });

    syncIsrcVisibility();
  }

  // --- Live Preview ---
  function schedulePreview() {
    if (state.previewTimer) clearTimeout(state.previewTimer);
    state.previewTimer = setTimeout(updatePreview, 180);
  }

  async function updatePreview() {
    const seq = ++state.previewSeq;
    try {
      const data = await apiJson("/api/preview-paths", {
        method: "POST",
        body: JSON.stringify({
          album: els.albumTitle.value.trim(),
          album_artist: els.albumArtist.value.trim(),
          date: els.releaseDate.value.trim(),
          status: els.releaseStatus.value,
          output_dir: els.outputDir.value.trim(),
          wav_conversion: selectedWavConversion(),
          tracks: state.tracks.map((t) => ({
            filename: t.filename,
            title: t.title,
            track_number: t.trackNumber,
            disc_number: t.discNumber,
          })),
        }),
      });

      if (seq !== state.previewSeq) return;

      els.previewFolder.textContent = `${data.album_dir}/`;
      els.previewFiles.innerHTML = "";

      for (const t of data.tracks) {
        const li = document.createElement("li");
        const note = t.will_convert ? `WAV → ${t.target_format.toUpperCase()}` : t.target_format.toUpperCase();
        li.innerHTML = `<span>${escapeHtml(t.output_filename)}</span><span class="preview-extra">${escapeHtml(note)}</span>`;
        els.previewFiles.appendChild(li);
      }

      const extras = ["release-info.md", "release-info.json"];
      if (state.coverFile) extras.unshift("cover.jpg");
      for (const extra of extras) {
        const li = document.createElement("li");
        li.className = "preview-extra";
        li.innerHTML = `<span>${extra}</span><span>generado</span>`;
        els.previewFiles.appendChild(li);
      }
    } catch (err) {
      if (seq !== state.previewSeq) return;
      els.previewFolder.textContent = err.message;
      els.previewFiles.innerHTML = "";
    }
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function formatSeconds(sec) {
    if (typeof sec !== "number" || Number.isNaN(sec)) return "—";
    const mins = Math.floor(sec / 60);
    const rem = Math.round(sec % 60);
    return `${String(mins).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
  }

  // --- Verification Report ---
  function renderVerificationReport(result) {
    state.lastExportedDir = result.album_dir;
    els.verificationCard.classList.remove("hidden");
    els.verificationCard.classList.toggle("has-error", !result.all_verified);

    if (result.all_verified) {
      els.verificationBadge.className = "badge-pass";
      els.verificationBadge.textContent = "✓ Verificado";
      els.verificationHeading.textContent = `${result.tracks.length} pista(s) creadas y verificadas`;
    } else {
      els.verificationBadge.className = "badge-fail";
      els.verificationBadge.textContent = "Revisar";
      els.verificationHeading.textContent = "Algunas pistas presentaron discrepancias";
    }

    els.verificationPath.textContent = result.album_dir;
    els.verificationList.innerHTML = "";

    for (const track of result.tracks) {
      const li = document.createElement("li");
      li.className = "verification-item";
      const badgeClass = track.verified ? "badge-pass" : "badge-fail";
      const badgeText = track.verified ? "✓ Verificado" : "Error";
      const coverText = track.has_cover ? "con portada" : "sin portada";
      const convText = track.converted ? ` · convertido a ${track.format.toUpperCase()}` : "";

      let errorHtml = "";
      if (Array.isArray(track.mismatches) && track.mismatches.length > 0) {
        const details = track.mismatches
          .map((m) => `${m.field}: esperado ${JSON.stringify(m.expected)}, leído ${JSON.stringify(m.actual)}`)
          .join(" · ");
        errorHtml = `<div class="verification-error-detail">${escapeHtml(details)}</div>`;
      }

      li.innerHTML = `
        <div class="verification-item-main">
          <span class="verification-item-file">${escapeHtml(track.filename)}</span>
          <span class="verification-item-meta">${formatSeconds(track.duration_seconds)} · ${coverText}${escapeHtml(convText)}</span>
          ${errorHtml}
        </div>
        <span class="${badgeClass}">${badgeText}</span>
      `;
      els.verificationList.appendChild(li);
    }

    els.btnOpenFinderBar.classList.remove("hidden");
  }

  // --- Process & Export ---
  async function runProcess() {
    const album = els.albumTitle.value.trim();
    const albumArtist = els.albumArtist.value.trim();
    const artist = els.releaseArtist.value.trim() || albumArtist;
    const date = els.releaseDate.value.trim();
    const outputDir = els.outputDir.value.trim();
    const genres = Array.from(state.selectedGenres);

    if (!album) return setStatus("Escribe el título del álbum.", "error");
    if (!albumArtist) return setStatus("Escribe el artista del álbum.", "error");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      return setStatus("La fecha debe tener formato YYYY-MM-DD.", "error");
    }
    if (genres.length === 0) return setStatus("Selecciona al menos un género.", "error");
    if (!outputDir) return setStatus("Indica la carpeta de salida.", "error");
    if (state.tracks.length === 0) return setStatus("Añade al menos un archivo de audio.", "error");

    const missingFile = state.tracks.find((t) => !t.file);
    if (missingFile) {
      return setStatus("Vuelve a arrastrar los archivos de audio antes de exportar.", "error");
    }

    const payload = {
      album,
      album_artist: albumArtist,
      artist,
      date,
      original_date: els.originalDate.value.trim() || null,
      genre: genres,
      release_type: els.releaseType.value,
      status: els.releaseStatus.value,
      label: els.releaseLabel.value.trim() || null,
      catalog_number: els.catalogNumber.value.trim() || null,
      copyright: els.releaseCopyright.value.trim() || null,
      distribution: {
        primary_genre: els.distPrimaryGenre.value || null,
        secondary_genre: els.distSecondaryGenre.value || null,
        language: els.distLanguage.value.trim() || null,
        instrumental: els.distInstrumental.checked,
        explicit: els.distExplicit.checked,
        upc: els.distUpc.value.trim() || null,
        credits: els.distCredits.value.trim() || null,
      },
      output_dir: outputDir,
      wav_conversion: selectedWavConversion(),
      tracks: state.tracks.map((t) => ({
        title: t.title.trim(),
        artist: t.artist.trim() || artist,
        track_number: t.trackNumber,
        disc_number: t.discNumber,
        isrc: t.isrc.trim() || null,
        original_filename: t.filename,
      })),
    };

    const formData = new FormData();
    formData.append("payload", JSON.stringify(payload));
    for (const track of state.tracks) {
      formData.append("audio_files", track.file, track.filename);
    }
    if (state.coverFile) {
      formData.append("cover_file", state.coverFile, state.coverFile.name);
    }

    els.btnProcess.disabled = true;
    els.btnProcess.textContent = "Procesando y verificando…";
    setStatus("Creando copias limpias y verificando metadata…");

    try {
      const res = await fetch("/api/process", { method: "POST", body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `Error HTTP ${res.status}`);
      }
      renderVerificationReport(data);
      if (data.all_verified) {
        setStatus("Listo: copias generadas y 100% verificadas.", "ok");
      } else {
        setStatus("Exportación completada con advertencias de verificación.", "error");
      }
    } catch (err) {
      setStatus(err.message || "Error al procesar el lanzamiento.", "error");
    } finally {
      els.btnProcess.disabled = false;
      els.btnProcess.textContent = "Crear copias y verificar";
    }
  }

  // --- Open / Pick Folder ---
  async function pickFolder() {
    els.btnPickFolder.disabled = true;
    try {
      const data = await apiJson("/api/pick-folder", {
        method: "POST",
        body: JSON.stringify({ initial_dir: els.outputDir.value.trim() || null }),
      });
      if (data.selected && data.path) {
        els.outputDir.value = data.path;
        schedulePreview();
        setStatus(`Carpeta de salida guardada: ${data.path}`);
      } else if (data.message && !data.cancelled) {
        setStatus(data.message);
      }
    } catch (err) {
      setStatus(err.message, "error");
    } finally {
      els.btnPickFolder.disabled = false;
    }
  }

  async function openLastFolder() {
    if (!state.lastExportedDir) return;
    try {
      await apiJson("/api/open-folder", {
        method: "POST",
        body: JSON.stringify({ path: state.lastExportedDir }),
      });
    } catch (err) {
      setStatus(err.message, "error");
    }
  }

  // --- Import release-info (.json / .md) ---
  async function importReleaseInfoFile(file) {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file, file.name);
    try {
      const res = await fetch("/api/import-release-info", { method: "POST", body: formData });
      const doc = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(doc.detail || `Error HTTP ${res.status}`);

      els.albumTitle.value = doc.album || "";
      els.albumArtist.value = doc.album_artist || "";
      els.releaseArtist.value = doc.artist || doc.album_artist || "";
      els.releaseDate.value = doc.date || todayIso();
      els.originalDate.value = doc.original_date && doc.original_date !== doc.date ? doc.original_date : "";
      if (doc.release_type) els.releaseType.value = doc.release_type;
      if (doc.status) els.releaseStatus.value = doc.status;
      els.releaseLabel.value = doc.label || "";
      els.catalogNumber.value = doc.catalog_number || "";
      els.releaseCopyright.value = doc.copyright || "";

      state.selectedGenres = new Set(Array.isArray(doc.genre) ? doc.genre : []);
      renderGenres();

      const dist = doc.distribution || {};
      els.distPrimaryGenre.value = dist.primary_genre || "";
      els.distSecondaryGenre.value = dist.secondary_genre || "";
      els.distLanguage.value = dist.language || "";
      els.distUpc.value = dist.upc || "";
      els.distInstrumental.checked = Boolean(dist.instrumental);
      els.distExplicit.checked = Boolean(dist.explicit);
      els.distCredits.value = dist.credits || "";

      // Si ya hay archivos cargados, emparejar primero por (disc, track) o nombre de archivo antes de índice
      if (Array.isArray(doc.tracks) && state.tracks.length > 0) {
        const usedTrackIds = new Set();

        const applyImportedToTrack = (target, imported) => {
          target.title = imported.title || target.title;
          target.artist = imported.artist || target.artist;
          target.discNumber = imported.disc_number || target.discNumber || 1;
          target.isrc = imported.isrc || "";
          usedTrackIds.add(target.id);
        };

        const stemLower = (name) =>
          String(name || "")
            .replace(/\.[^.]+$/, "")
            .trim()
            .toLowerCase();

        const unmatchedImported = [];
        for (const imported of doc.tracks) {
          const impDisc = Number(imported.disc_number) || 1;
          const impTrackNum = Number(imported.track_number) || 0;
          const impFileLower = String(imported.filename || "").trim().toLowerCase();
          const impStem = stemLower(imported.filename);

          let match = state.tracks.find(
            (t) =>
              !usedTrackIds.has(t.id) &&
              t.discNumber === impDisc &&
              t.trackNumber === impTrackNum
          );
          if (!match && impFileLower) {
            match = state.tracks.find(
              (t) =>
                !usedTrackIds.has(t.id) &&
                (t.filename.trim().toLowerCase() === impFileLower ||
                  (impStem && stemLower(t.filename) === impStem))
            );
          }

          if (match) {
            applyImportedToTrack(match, imported);
          } else {
            unmatchedImported.push(imported);
          }
        }

        const remainingTracks = state.tracks.filter((t) => !usedTrackIds.has(t.id));
        unmatchedImported.forEach((imported, idx) => {
          if (remainingTracks[idx]) {
            applyImportedToTrack(remainingTracks[idx], imported);
          }
        });

        renderTracks();
      }

      schedulePreview();
      setStatus(`Datos importados desde ${file.name}.`, "ok");
    } catch (err) {
      setStatus(err.message, "error");
    } finally {
      els.importInfoInput.value = "";
    }
  }

  // --- Wire dropzones ---
  function setupDropzone(el, onFiles) {
    ["dragenter", "dragover"].forEach((evt) => {
      el.addEventListener(evt, (e) => {
        e.preventDefault();
        el.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach((evt) => {
      el.addEventListener(evt, (e) => {
        e.preventDefault();
        el.classList.remove("drag-over");
      });
    });
    el.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        onFiles(e.dataTransfer.files);
      }
    });
  }

  // --- Event Listeners ---
  function bindEvents() {
    els.btnToday.addEventListener("click", () => {
      els.releaseDate.value = todayIso();
      if (state.activeCopyrightTemplate) {
        els.releaseCopyright.value = state.activeCopyrightTemplate.replaceAll("{year}", currentYear());
      }
      schedulePreview();
    });

    els.releaseDate.addEventListener("input", () => {
      if (state.activeCopyrightTemplate && /^\d{4}/.test(els.releaseDate.value.trim())) {
        els.releaseCopyright.value = state.activeCopyrightTemplate.replaceAll("{year}", currentYear());
      }
      schedulePreview();
    });

    els.albumArtist.addEventListener("input", () => {
      if (!state.artistTouched) {
        els.releaseArtist.value = els.albumArtist.value;
      }
      schedulePreview();
    });

    els.releaseArtist.addEventListener("input", () => {
      state.artistTouched = els.releaseArtist.value.trim() !== els.albumArtist.value.trim();
    });

    [els.albumTitle, els.releaseStatus, els.outputDir].forEach((input) => {
      input.addEventListener("input", schedulePreview);
      input.addEventListener("change", schedulePreview);
    });

    els.outputDir.addEventListener("blur", () => {
      const val = els.outputDir.value.trim();
      if (val) {
        apiJson("/api/settings/output-dir", {
          method: "PUT",
          body: JSON.stringify({ output_dir: val }),
        }).catch(() => {});
      }
    });

    document.querySelectorAll('input[name="wav-conversion"]').forEach((radio) => {
      radio.addEventListener("change", schedulePreview);
    });

    els.btnAddGenre.addEventListener("click", addNewGenre);
    els.newGenreInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addNewGenre();
      }
    });

    // Cover
    els.coverDropzone.addEventListener("click", (e) => {
      if (e.target === els.btnRemoveCover) return;
      els.coverInput.click();
    });
    els.coverDropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        els.coverInput.click();
      }
    });
    els.coverInput.addEventListener("change", () => {
      if (els.coverInput.files && els.coverInput.files[0]) {
        setCoverFile(els.coverInput.files[0]);
      }
    });
    els.btnRemoveCover.addEventListener("click", (e) => {
      e.stopPropagation();
      clearCoverFile();
    });
    setupDropzone(els.coverDropzone, (files) => {
      if (files[0]) setCoverFile(files[0]);
    });

    // Audio dropzone
    els.audioDropzone.addEventListener("click", () => els.audioInput.click());
    els.audioDropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        els.audioInput.click();
      }
    });
    els.audioInput.addEventListener("change", () => {
      if (els.audioInput.files && els.audioInput.files.length > 0) {
        addAudioFiles(els.audioInput.files);
        els.audioInput.value = "";
      }
    });
    setupDropzone(els.audioDropzone, addAudioFiles);

    // Track toolbar
    els.btnCleanAll.addEventListener("click", async () => {
      if (state.tracks.length === 0) return;
      const snapshot = state.tracks.map((t) => ({ id: t.id, filename: t.filename }));
      try {
        const data = await apiJson("/api/clean-names", {
          method: "POST",
          body: JSON.stringify({ filenames: snapshot.map((item) => item.filename) }),
        });
        const cleanedById = new Map();
        snapshot.forEach((item, idx) => {
          if (typeof data.cleaned?.[idx] === "string") {
            cleanedById.set(item.id, data.cleaned[idx]);
          }
        });
        for (const track of state.tracks) {
          if (cleanedById.has(track.id)) {
            track.title = cleanedById.get(track.id);
          }
        }
        renderTracks();
        schedulePreview();
        setStatus("Nombres de pista limpiados respetando mayúsculas.");
      } catch (err) {
        setStatus(err.message, "error");
      }
    });

    els.btnClearTracks.addEventListener("click", () => {
      state.tracks = [];
      renderTracks();
      schedulePreview();
    });

    els.distributionAccordion.addEventListener("toggle", syncIsrcVisibility);

    // Folders & Import & Process
    els.btnPickFolder.addEventListener("click", pickFolder);
    els.btnOpenFinderCard.addEventListener("click", openLastFolder);
    els.btnOpenFinderBar.addEventListener("click", openLastFolder);
    els.btnImportInfo.addEventListener("click", () => els.importInfoInput.click());
    els.importInfoInput.addEventListener("change", () => {
      if (els.importInfoInput.files && els.importInfoInput.files[0]) {
        importReleaseInfoFile(els.importInfoInput.files[0]);
      }
    });
    els.btnProcess.addEventListener("click", runProcess);

    // Save Profile modal
    els.btnSaveProfile.addEventListener("click", () => {
      els.newProfileName.value = els.albumArtist.value.trim()
        ? `${els.albumArtist.value.trim()}${els.releaseLabel.value.trim() ? ` (${els.releaseLabel.value.trim()})` : ""}`
        : "";
      els.newProfileCatalogPrefix.value = "";
      els.profileDialog.showModal();
    });
    els.btnCancelProfile.addEventListener("click", () => els.profileDialog.close());
    els.profileForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = els.newProfileName.value.trim();
      const artist = els.releaseArtist.value.trim() || els.albumArtist.value.trim();
      const albumArtist = els.albumArtist.value.trim() || artist;
      if (!name || !artist) {
        setStatus("Completa nombre del perfil y artista antes de guardar.", "error");
        return;
      }
      const slug = name
        .toLowerCase()
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || `perfil-${Date.now()}`;

      const year = currentYear();
      const rawCopy = els.releaseCopyright.value.trim();
      const templateCopy = rawCopy ? rawCopy.replaceAll(year, "{year}") : "";

      try {
        const res = await apiJson("/api/presets/profiles", {
          method: "POST",
          body: JSON.stringify({
            id: slug,
            name,
            artist,
            album_artist: albumArtist,
            label: els.releaseLabel.value.trim(),
            copyright: templateCopy,
            release_type: els.releaseType.value,
            catalog_prefix: els.newProfileCatalogPrefix.value.trim(),
          }),
        });
        state.profiles = res.profiles;
        state.activeProfileId = slug;
        renderProfiles();
        els.profileDialog.close();
        setStatus(`Perfil guardado: ${name}`, "ok");
      } catch (err) {
        setStatus(err.message, "error");
      }
    });
  }

  // --- Bootstrap ---
  async function init() {
    els.releaseDate.value = todayIso();
    bindEvents();

    try {
      const [health, profilesData, genresData, settingsData] = await Promise.all([
        apiJson("/api/health"),
        apiJson("/api/presets/profiles"),
        apiJson("/api/presets/genres"),
        apiJson("/api/settings/output-dir"),
      ]);

      if (health.ffmpeg) {
        els.ffmpegBadge.textContent = "Conversión lista";
        els.ffmpegBadge.classList.add("pill-ok");
      } else {
        els.ffmpegBadge.textContent = "Sin ffmpeg (solo mantener formato)";
        els.ffmpegBadge.classList.add("pill-err");
      }

      const openLabel = health.platform === "darwin" ? "Abrir carpeta en Finder" : "Abrir carpeta";
      els.btnOpenFinderCard.textContent = openLabel;
      els.btnOpenFinderBar.textContent = openLabel;

      state.profiles = profilesData.profiles || [];
      state.genresCatalog = genresData;
      els.outputDir.value = settingsData.output_dir || "";

      renderProfiles();
      renderGenres();
      updatePreview();
    } catch (err) {
      setStatus(`Error inicializando Meta Studio: ${err.message}`, "error");
    }
  }

  init();
})();
