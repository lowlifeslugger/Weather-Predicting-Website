if (!getToken()) {
  window.location.href = "/";
}

let notebookId = null;
let currentMode = "files";

document.getElementById("logout-btn").addEventListener("click", () => {
  clearToken();
  window.location.href = "/";
});

async function init() {
  await loadMe();
  await loadNotebook();
  await Promise.all([loadDocuments(), loadNotes()]);
  setupDropzone();
  setupChat();
}

async function loadMe() {
  const res = await apiFetch("/auth/me");
  const me = await res.json();
  const used = me.storage_used_bytes;
  const limit = me.storage_limit_bytes || 1;
  const pct = Math.min(100, (used / limit) * 100);
  document.getElementById("quota-label").textContent =
    `${formatBytes(used)} / ${formatBytes(limit)}`;
  const fill = document.getElementById("quota-fill");
  fill.style.width = `${pct}%`;
  fill.className = "quota-fill" + (pct > 90 ? " danger" : pct > 70 ? " warn" : "");
}

async function loadNotebook() {
  const res = await apiFetch("/notebooks");
  const notebooks = await res.json();
  // Every account has exactly one notebook (created automatically at
  // signup) -- just use it.
  const nb = notebooks[0];
  notebookId = nb.id;
  document.getElementById("notebook-name").textContent = nb.name;
}

// ── Files ──────────────────────────────────────────────────────────────

function setupDropzone() {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("file-input");

  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files.length) uploadFile(input.files[0]);
    input.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    })
  );
  zone.addEventListener("drop", (e) => {
    const files = e.dataTransfer.files;
    if (files.length) uploadFile(files[0]);
  });
}

async function uploadFile(file) {
  const zone = document.getElementById("dropzone");
  const original = zone.innerHTML;
  zone.innerHTML = `Indexing ${escapeHtml(file.name)}...`;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await apiFetch(`/notebooks/${notebookId}/documents`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Upload failed");
    }
    await loadDocuments();
    await loadMe(); // quota bar changed
  } catch (err) {
    alert(err.message);
  } finally {
    zone.innerHTML = original;
  }
}

async function loadDocuments() {
  const res = await apiFetch(`/notebooks/${notebookId}/documents`);
  const docs = await res.json();
  const list = document.getElementById("file-list");

  if (docs.length === 0) {
    list.innerHTML = '<div class="empty-note">No files yet.</div>';
    return;
  }

  list.innerHTML = docs
    .map(
      (d) => `
      <div class="file-row">
        <div>
          <div class="name">${escapeHtml(d.filename)}</div>
          <div class="meta">${formatBytes(d.file_size)} · ${d.chunk_count} chunks</div>
        </div>
        <div class="row-actions">
          <button onclick="downloadDocument('${d.id}', '${escapeHtml(d.filename)}')">Get</button>
          <button class="danger" onclick="deleteDocument('${d.id}')">Del</button>
        </div>
      </div>`
    )
    .join("");
}

async function downloadDocument(docId, filename) {
  const res = await apiFetch(`/notebooks/${notebookId}/documents/${docId}/download`);
  if (!res.ok) return alert("Couldn't download that file.");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

async function deleteDocument(docId) {
  if (!confirm("Delete this file?")) return;
  await apiFetch(`/notebooks/${notebookId}/documents/${docId}`, { method: "DELETE" });
  await loadDocuments();
  await loadMe();
}

// ── Notes ──────────────────────────────────────────────────────────────

async function loadNotes() {
  const res = await apiFetch(`/notebooks/${notebookId}/notes`);
  const notes = await res.json();
  const list = document.getElementById("note-list");

  if (notes.length === 0) {
    list.innerHTML = '<div class="empty-note">No notes yet.</div>';
    return;
  }

  list.innerHTML = notes
    .map(
      (n) => `
      <div class="note-row" id="note-row-${n.id}">
        <div class="name">${escapeHtml(n.title)}</div>
        <div class="row-actions">
          <button onclick="openNoteEditor('${n.id}', ${JSON.stringify(n.title)}, ${JSON.stringify(n.content)})">Edit</button>
          <button class="danger" onclick="deleteNote('${n.id}')">Del</button>
        </div>
      </div>`
    )
    .join("");
}

document.getElementById("new-note-btn").addEventListener("click", () => {
  openNoteEditor(null, "Untitled note", "");
});

function openNoteEditor(noteId, title, content) {
  const list = document.getElementById("note-list");
  const editorId = "note-editor-active";
  document.getElementById(editorId)?.remove();

  const editor = document.createElement("div");
  editor.className = "note-editor";
  editor.id = editorId;
  editor.innerHTML = `
    <input type="text" id="editor-title" value="${escapeHtml(title)}">
    <textarea id="editor-content">${escapeHtml(content)}</textarea>
    <div class="note-actions">
      <button id="editor-cancel">Cancel</button>
      <button class="primary" id="editor-save">Save</button>
    </div>
  `;
  list.prepend(editor);

  document.getElementById("editor-cancel").addEventListener("click", () => editor.remove());
  document.getElementById("editor-save").addEventListener("click", async () => {
    const newTitle = document.getElementById("editor-title").value || "Untitled note";
    const newContent = document.getElementById("editor-content").value;
    const saveBtn = document.getElementById("editor-save");
    saveBtn.disabled = true;
    try {
      if (noteId) {
        await apiFetch(`/notebooks/${notebookId}/notes/${noteId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: newTitle, content: newContent }),
        });
      } else {
        await apiFetch(`/notebooks/${notebookId}/notes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: newTitle, content: newContent }),
        });
      }
      editor.remove();
      await loadNotes();
    } catch (err) {
      alert("Couldn't save that note.");
    } finally {
      saveBtn.disabled = false;
    }
  });
}

async function deleteNote(noteId) {
  if (!confirm("Delete this note?")) return;
  await apiFetch(`/notebooks/${notebookId}/notes/${noteId}`, { method: "DELETE" });
  await loadNotes();
}

// ── Chat ───────────────────────────────────────────────────────────────

function setupChat() {
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentMode = btn.dataset.mode;
    });
  });

  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");

  const send = () => {
    const query = input.value.trim();
    if (!query) return;
    input.value = "";
    askQuestion(query);
  };

  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });
}

function renderSources(sourcesEl, sources) {
  const tags = [];
  (sources.files || []).forEach((s) => tags.push(`<span class="source-tag">${escapeHtml(s.source)}</span>`));
  (sources.web || []).forEach((s) => tags.push(`<span class="source-tag web">${escapeHtml(s.title || s.url)}</span>`));
  sourcesEl.innerHTML = tags.join("");
}

async function askQuestion(query) {
  const log = document.getElementById("chat-log");
  const turn = document.createElement("div");
  turn.className = "chat-turn";
  turn.innerHTML = `
    <div class="q">${escapeHtml(query)}</div>
    <div class="sources-row"></div>
    <div class="answer streaming"></div>
  `;
  log.appendChild(turn);
  log.scrollTop = log.scrollHeight;

  const sourcesEl = turn.querySelector(".sources-row");
  const answerEl = turn.querySelector(".answer");

  const sendBtn = document.getElementById("chat-send");
  sendBtn.disabled = true;

  try {
    const res = await apiFetch(`/notebooks/${notebookId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, mode: currentMode }),
    });

    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Chat request failed");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by a blank line.
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);

        const lines = block.split("\n");
        const eventLine = lines.find((l) => l.startsWith("event: "));
        const dataLine = lines.find((l) => l.startsWith("data: "));
        if (!eventLine || !dataLine) continue;

        const event = eventLine.slice("event: ".length);
        const data = JSON.parse(dataLine.slice("data: ".length));

        if (event === "sources") {
          renderSources(sourcesEl, data);
        } else if (event === "token") {
          answerEl.textContent += data.content;
          log.scrollTop = log.scrollHeight;
        } else if (event === "error") {
          answerEl.classList.remove("streaming");
          const errEl = document.createElement("div");
          errEl.className = "chat-error";
          errEl.textContent = data.message;
          turn.appendChild(errEl);
        } else if (event === "done") {
          answerEl.classList.remove("streaming");
        }
      }
    }
  } catch (err) {
    answerEl.classList.remove("streaming");
    const errEl = document.createElement("div");
    errEl.className = "chat-error";
    errEl.textContent = err.message;
    turn.appendChild(errEl);
  } finally {
    sendBtn.disabled = false;
  }
}

init();
