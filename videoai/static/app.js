/* VideoAI — front-end logic (tanpa framework, tanpa dependensi) */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    form: $("form"), topic: $("topic"), topicCount: $("topic-count"),
    sceneCount: $("scene_count"), scenesOut: $("scenes-out"), estDuration: $("est-duration"),
    btnGenerate: $("btn-generate"), btnScript: $("btn-script"),
    btnVoicePreview: $("btn-voice-preview"), audioPreview: $("audio-preview"),
    health: $("health"),
    views: {
      empty: $("view-empty"), progress: $("view-progress"),
      script: $("view-script"), result: $("view-result"), error: $("view-error"),
    },
    barFill: $("bar-fill"), progressMsg: $("progress-msg"),
    progressTitle: $("progress-title"), progressTime: $("progress-time"),
    progressLog: $("progress-log"),
    video: $("video"), resultTitle: $("result-title"), resultDesc: $("result-desc"),
    statDuration: $("stat-duration"), statSize: $("stat-size"),
    statScenes: $("stat-scenes"), statElapsed: $("stat-elapsed"),
    btnDownload: $("btn-download"), btnAgain: $("btn-again"),
    btnRetry: $("btn-retry"), errorMsg: $("error-msg"),
    scriptTitle: $("script-title"), scriptDesc: $("script-desc"),
    scriptScenes: $("script-scenes"), scriptWarning: $("script-warning"),
    btnScriptUse: $("btn-script-use"), btnScriptCancel: $("btn-script-cancel"),
    resultScenes: $("result-scenes"),
    btnHistory: $("btn-history"), history: $("history"), historyList: $("history-list"),
    language: $("language"), voiceGender: $("voice_gender"),
  };

  let currentScript = null;
  let activeJobId = null;
  let eventSource = null;
  let timer = null;

  /* ---------------------------------------------------------- helpers */
  const show = (name) => {
    Object.entries(el.views).forEach(([k, node]) => node.classList.toggle("hidden", k !== name));
  };

  const fmtDuration = (s) => {
    if (!s && s !== 0) return "—";
    const total = Math.round(s);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  };

  const fmtSize = (b) => {
    if (!b) return "—";
    const mb = b / 1e6;
    return mb >= 1000 ? `${(mb / 1000).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
  };

  const fmtTime = (ms) => new Date(ms * 1000).toLocaleString("id-ID", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });

  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

  const readForm = () => ({
    topic: el.topic.value.trim(),
    language: el.language.value,
    voice_gender: el.voiceGender.value,
    scene_count: Number(el.sceneCount.value),
    aspect: $("aspect").value,
    quality: $("quality").value,
    visual_style: $("visual_style").value,
    tone: $("tone").value.trim(),
    extra_direction: $("extra_direction").value.trim(),
    image_model: $("image_model").value,
    music_mood: $("music_mood").value,
    seed: Number($("seed").value) || 0,
    enable_motion: $("enable_motion").checked,
    enable_narration: $("enable_narration").checked,
    enable_subtitles: $("enable_subtitles").checked,
    enable_music: $("enable_music").checked,
  });

  const showError = (msg) => {
    el.errorMsg.textContent = msg || "Kesalahan tidak diketahui.";
    show("error");
  };

  /* ---------------------------------------------------------- UI kecil */
  const syncRange = () => {
    const n = Number(el.sceneCount.value);
    el.scenesOut.textContent = n;
    // Perkiraan kasar: bergantung kualitas + pembatasan laju layanan gratis.
    const perScene = { draft: 18, high: 42, ultra: 75 }[$("quality").value] ?? 42;
    el.estDuration.textContent = `perkiraan proses ${fmtDuration(n * perScene + 15)}`;
  };

  el.topic.addEventListener("input", () => {
    el.topicCount.textContent = el.topic.value.length;
  });
  el.sceneCount.addEventListener("input", syncRange);
  $("quality").addEventListener("change", syncRange);
  syncRange();
  el.topicCount.textContent = el.topic.value.length;

  document.querySelectorAll("#idea-chips .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      el.topic.value = chip.dataset.idea;
      el.topic.dispatchEvent(new Event("input"));
      el.topic.focus();
    });
  });

  el.topic.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
      ev.preventDefault();
      el.form.requestSubmit();
    }
  });

  /* ---------------------------------------------------------- health */
  (async () => {
    try {
      const res = await fetch("/healthz");
      const data = await res.json();
      const ok = data.ffmpeg;
      el.health.textContent = ok
        ? `siap · FFmpeg ✓ · ${data.outputs} video`
        : "FFmpeg tidak ditemukan";
      el.health.className = `pill ${ok ? "pill-ok" : "pill-err"}`;
      el.health.title = ok
        ? `v${data.version} — antrean: ${data.queue}`
        : (data.ffmpeg_error || "");
    } catch {
      el.health.textContent = "server tidak terjangkau";
      el.health.className = "pill pill-err";
    }
  })();

  /* ---------------------------------------------------------- suara */
  el.btnVoicePreview.addEventListener("click", async () => {
    el.btnVoicePreview.disabled = true;
    el.btnVoicePreview.textContent = "…";
    try {
      const res = await fetch("/api/voices/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          language: el.language.value,
          voice_gender: el.voiceGender.value,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
      const blob = await res.blob();
      el.audioPreview.src = URL.createObjectURL(blob);
      await el.audioPreview.play();
    } catch (err) {
      showError(`Gagal memuat contoh suara: ${err.message}`);
    } finally {
      el.btnVoicePreview.disabled = false;
      el.btnVoicePreview.textContent = "▶︎";
    }
  });

  /* ---------------------------------------------------------- skrip */
  el.btnScript.addEventListener("click", async () => {
    if (!el.topic.value.trim()) {
      el.topic.focus();
      el.topic.classList.add("warn");
      setTimeout(() => el.topic.classList.remove("warn"), 1200);
      return;
    }
    el.btnScript.disabled = true;
    el.btnScript.textContent = "⏳ Menyusun skrip…";
    try {
      const res = await fetch("/api/script", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readForm()),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      currentScript = data;
      renderScriptEditor(data);
      show("script");
    } catch (err) {
      showError(`Gagal membuat skrip: ${err.message}`);
    } finally {
      el.btnScript.disabled = false;
      el.btnScript.textContent = "📝 Lihat skrip dulu";
    }
  });

  function renderScriptEditor(board) {
    el.scriptTitle.value = board.title || "";
    el.scriptDesc.value = board.description || "";
    if (board.warning) {
      el.scriptWarning.textContent = `⚠️ ${board.warning}`;
      el.scriptWarning.classList.remove("hidden");
    } else {
      el.scriptWarning.classList.add("hidden");
    }
    el.scriptScenes.innerHTML = board.scenes.map((s, i) => `
      <div class="scene-card" data-index="${i}">
        <span class="scene-head">Scene ${i + 1}</span>
        <label>Narasi (dibacakan)</label>
        <textarea class="scene-narration" rows="2">${escapeHtml(s.narration)}</textarea>
        <label>Prompt gambar (English)</label>
        <textarea class="scene-prompt" rows="2">${escapeHtml(s.image_prompt)}</textarea>
      </div>`).join("");
  }

  el.btnScriptCancel.addEventListener("click", () => { currentScript = null; show("empty"); });

  el.btnScriptUse.addEventListener("click", () => {
    if (!currentScript) return;
    const cards = [...el.scriptScenes.querySelectorAll(".scene-card")];
    const script = {
      title: el.scriptTitle.value.trim(),
      description: el.scriptDesc.value.trim(),
      scenes: cards.map((card) => ({
        narration: card.querySelector(".scene-narration").value.trim(),
        image_prompt: card.querySelector(".scene-prompt").value.trim(),
        on_screen_text: "",
      })).filter((s) => s.narration),
    };
    if (!script.scenes.length) { showError("Semua scene kosong — isi minimal satu narasi."); return; }
    startJob({ ...readForm(), topic: el.topic.value.trim() || script.title, script });
  });

  /* ---------------------------------------------------------- buat video */
  el.form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!el.topic.value.trim()) { el.topic.focus(); return; }
    startJob({ ...readForm(), script: null });
  });

  async function startJob(payload) {
    if (eventSource) { eventSource.close(); eventSource = null; }
    el.btnGenerate.disabled = true;
    el.btnGenerate.textContent = "⏳ Membuat…";
    el.progressLog.innerHTML = "";
    el.barFill.style.width = "0%";
    el.progressMsg.textContent = "Mengirim permintaan…";
    el.progressTitle.textContent = "Sedang mengerjakan…";
    show("progress");
    startElapsedTimer();

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      activeJobId = data.job_id;
      listenToJob(activeJobId);
    } catch (err) {
      stopElapsedTimer();
      finishButtons();
      showError(`Gagal memulai: ${err.message}`);
    }
  }

  function listenToJob(jobId) {
    eventSource = new EventSource(`/api/jobs/${jobId}/events`);
    eventSource.onmessage = (ev) => {
      let job;
      try { job = JSON.parse(ev.data); } catch { return; }
      renderJob(job);
      if (job.status === "done" || job.status === "error") {
        eventSource.close();
        eventSource = null;
        stopElapsedTimer();
        finishButtons();
        loadHistory();
      }
    };
    eventSource.onerror = () => {
      // SSE terputus — coba polling sekali sebagai jaring pengaman.
      eventSource?.close();
      eventSource = null;
      pollOnce(jobId);
    };
  }

  async function pollOnce(jobId) {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();
      renderJob(job);
      if (job.status === "running" || job.status === "queued") {
        setTimeout(() => pollOnce(jobId), 1500);
      } else {
        stopElapsedTimer();
        finishButtons();
        loadHistory();
      }
    } catch {
      stopElapsedTimer();
      finishButtons();
      showError("Koneksi ke server terputus saat memantau progres.");
    }
  }

  function renderJob(job) {
    el.barFill.style.width = `${Math.min(100, job.progress * 100).toFixed(1)}%`;
    el.progressMsg.textContent = job.message || "";
    if (job.logs?.length) {
      el.progressLog.innerHTML = job.logs.map((l) => `<li>${escapeHtml(l)}</li>`).join("");
      el.progressLog.scrollTop = el.progressLog.scrollHeight;
    }
    if (job.status === "error") {
      showError(job.error || job.message || "Job gagal.");
      return;
    }
    if (job.status === "done" && job.result) renderResult(job.result);
  }

  function renderResult(result) {
    el.video.src = `/outputs/${encodeURIComponent(result.video_path.split("/").pop())}`;
    el.resultTitle.textContent = result.title || "Video selesai";
    el.resultDesc.textContent = result.description || "";
    el.statDuration.textContent = fmtDuration(result.duration);
    el.statSize.textContent = fmtSize(result.size_bytes);
    el.statScenes.textContent = `${result.scenes?.length ?? 0}`;
    el.statElapsed.textContent = `${Math.round(result.elapsed)}s`;
    el.btnDownload.href = el.video.src;
    el.btnDownload.setAttribute("download", result.video_path.split("/").pop());

    el.resultScenes.innerHTML = (result.scenes || []).length ? `
      <summary>Rincian ${result.scenes.length} scene</summary>
      <ol>${result.scenes.map((s) => `
        <li><strong>${fmtDuration(s.start)}–${fmtDuration((s.start || 0) + (s.duration || 0))}</strong>
        — ${escapeHtml(s.on_screen_text || s.image_prompt || "")}
        <br><span class="muted">${escapeHtml(s.narration || "")}</span></li>`).join("")}</ol>` : "";
    show("result");
  }

  function startElapsedTimer() {
    const t0 = Date.now();
    stopElapsedTimer();
    timer = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      el.progressTime.textContent = `Berjalan ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} — video bisa memakan beberapa menit.`;
    }, 1000);
  }
  function stopElapsedTimer() { if (timer) { clearInterval(timer); timer = null; } }
  function finishButtons() {
    el.btnGenerate.disabled = false;
    el.btnGenerate.textContent = "✨ Buat Video";
  }

  el.btnAgain.addEventListener("click", () => { show("empty"); el.video.removeAttribute("src"); });
  el.btnRetry.addEventListener("click", () => el.form.requestSubmit());

  /* ---------------------------------------------------------- riwayat */
  el.btnHistory.addEventListener("click", () => {
    const hidden = el.history.classList.toggle("hidden");
    if (!hidden) loadHistory();
  });

  async function loadHistory() {
    try {
      const res = await fetch("/api/outputs");
      const items = await res.json();
      if (!items.length) {
        el.historyList.innerHTML = `<p class="muted small">Belum ada video. Buat video pertama Anda!</p>`;
        return;
      }
      el.historyList.innerHTML = items.map((v) => `
        <a class="history-item" href="${v.url}" target="_blank" rel="noopener">
          ${v.thumbnail ? `<img src="${v.thumbnail}" alt="" loading="lazy">`
                        : `<span class="history-thumb-fallback">🎬</span>`}
          <span class="meta">
            <strong>${escapeHtml(v.title)}</strong>
            <small>${fmtTime(v.modified)} · ${fmtDuration(v.duration)} · ${fmtSize(v.size_bytes)}</small>
          </span>
        </a>`).join("");
    } catch {
      el.historyList.innerHTML = `<p class="warn">Gagal memuat riwayat.</p>`;
    }
  }
  loadHistory();
})();