(function () {
  const shell = document.querySelector("[data-app-shell]");
  const memoryStorage = new Map();
  const storage = {
    getItem(key) {
      try {
        return window.localStorage ? window.localStorage.getItem(key) : memoryStorage.get(key) || null;
      } catch {
        return memoryStorage.get(key) || null;
      }
    },
    setItem(key, value) {
      try {
        if (window.localStorage) {
          window.localStorage.setItem(key, value);
          return;
        }
      } catch {}
      memoryStorage.set(key, value);
    },
  };

  function setSidebar(collapsed) {
    if (!shell) return;
    shell.classList.toggle("sidebar-collapsed", collapsed);
    storage.setItem("whackamole.sidebarCollapsed", collapsed ? "true" : "false");
  }

  function setMobileSidebar(open) {
    if (!shell) return;
    shell.classList.toggle("sidebar-open", open);
    document.body.classList.toggle("sidebar-modal-open", open);
  }

  if (shell) {
    setSidebar(storage.getItem("whackamole.sidebarCollapsed") === "true");
  }

  document.querySelectorAll("[data-sidebar-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 860px)").matches) {
        setMobileSidebar(!shell.classList.contains("sidebar-open"));
        return;
      }
      setSidebar(!shell.classList.contains("sidebar-collapsed"));
    });
  });

  document.querySelectorAll("[data-sidebar-close]").forEach((button) => {
    button.addEventListener("click", () => setMobileSidebar(false));
  });

  document.querySelectorAll(".sidebar-nav a, .sidebar-footer a").forEach((link) => {
    link.addEventListener("click", () => setMobileSidebar(false));
  });

  function setFilters(open) {
    document.body.classList.toggle("filters-open", open);
  }

  document.querySelectorAll("[data-filter-toggle]").forEach((button) => {
    button.addEventListener("click", () => setFilters(true));
  });

  document.querySelectorAll("[data-filter-close]").forEach((button) => {
    button.addEventListener("click", () => setFilters(false));
  });

  document.addEventListener("click", (event) => {
    if (!document.body.classList.contains("filters-open")) return;
    const panel = document.querySelector("[data-filter-panel]");
    const target = event.target;
    if (panel && target instanceof Node && !panel.contains(target) && !target.closest("[data-filter-toggle]")) {
      setFilters(false);
    }
  });

  const notificationMenu = document.querySelector("[data-notification-menu]");
  const notificationToggle = document.querySelector("[data-notification-toggle]");
  const notificationPopout = document.querySelector("[data-notification-popout]");
  if (notificationToggle && notificationPopout) {
    notificationToggle.addEventListener("click", (event) => {
      event.stopPropagation();
      notificationPopout.hidden = !notificationPopout.hidden;
    });
  }

  document.addEventListener("click", (event) => {
    if (!notificationMenu || !notificationPopout || notificationPopout.hidden) return;
    const target = event.target;
    if (target instanceof Node && !notificationMenu.contains(target)) {
      notificationPopout.hidden = true;
    }
  });

  document.querySelectorAll("[data-resizable-table]").forEach((table) => {
    const key = `whackamole.table.${table.dataset.resizableTable}.widths`;
    const headers = Array.from(table.querySelectorAll("th[data-column-key]"));
    const columns = new Map(Array.from(table.querySelectorAll("col[data-column-key]")).map((column) => [column.dataset.columnKey, column]));
    let saved = {};
    try {
      saved = JSON.parse(storage.getItem(key) || "{}");
    } catch {}
    const syncTableWidth = () => {
      const total = Array.from(columns.values()).reduce((sum, column) => {
        const explicit = Number.parseFloat(column.style.width || "");
        return sum + (Number.isFinite(explicit) && explicit > 0 ? explicit : column.getBoundingClientRect().width || 0);
      }, 0);
      if (total > 0) {
        table.style.width = `${Math.max(960, Math.round(total))}px`;
        table.style.minWidth = table.style.width;
      }
    };
    headers.forEach((header) => {
      const columnKey = header.dataset.columnKey;
      const column = columns.get(columnKey);
      if (!column) return;
      if (saved[columnKey]) column.style.width = `${saved[columnKey]}px`;
      const handle = document.createElement("span");
      handle.className = "column-resizer";
      handle.setAttribute("aria-hidden", "true");
      header.appendChild(handle);
      let startX = 0;
      let startWidth = 0;
      let startTableWidth = 0;
      handle.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        startX = event.clientX;
        startWidth = column.getBoundingClientRect().width || header.getBoundingClientRect().width;
        startTableWidth = table.getBoundingClientRect().width;
        table.style.width = `${Math.round(startTableWidth)}px`;
        table.style.minWidth = `${Math.round(startTableWidth)}px`;
        if (typeof handle.setPointerCapture === "function") {
          handle.setPointerCapture(event.pointerId);
        }
        document.body.classList.add("is-resizing-column");
      });
      handle.addEventListener("pointermove", (event) => {
        if (!document.body.classList.contains("is-resizing-column")) return;
        event.preventDefault();
        const width = Math.max(80, Math.round(startWidth + event.clientX - startX));
        const delta = width - startWidth;
        column.style.width = `${width}px`;
        table.style.width = `${Math.max(960, Math.round(startTableWidth + delta))}px`;
        table.style.minWidth = table.style.width;
      });
      handle.addEventListener("pointerup", (event) => {
        if (typeof handle.releasePointerCapture === "function") {
          handle.releasePointerCapture(event.pointerId);
        }
        document.body.classList.remove("is-resizing-column");
        let widths = {};
        try {
          widths = JSON.parse(storage.getItem(key) || "{}");
        } catch {}
        widths[columnKey] = Math.round(column.getBoundingClientRect().width || header.getBoundingClientRect().width);
        storage.setItem(key, JSON.stringify(widths));
      });
    });
    syncTableWidth();
  });

  document.querySelectorAll("[data-tabs]").forEach((tabs) => {
    const buttons = Array.from(tabs.querySelectorAll("[data-tab-target]"));
    const panels = Array.from(tabs.querySelectorAll("[data-tab-panel]"));
    const activate = (name) => {
      buttons.forEach((button) => button.classList.toggle("active", button.dataset.tabTarget === name));
      panels.forEach((panel) => panel.classList.toggle("active", panel.dataset.tabPanel === name));
    };

    buttons.forEach((button) => {
      button.addEventListener("click", () => activate(button.dataset.tabTarget));
    });

    const requested = window.location.hash.replace("#", "");
    if (requested && buttons.some((button) => button.dataset.tabTarget === requested)) {
      activate(requested);
    }
  });

  const modal = document.querySelector("[data-raw-modal]");
  const modalTitle = modal && modal.querySelector("#raw-modal-title");
  const rawOutput = modal && modal.querySelector("[data-raw-output]");
  let rawDownloadName = "whackamole-raw.txt";

  function modalTextFromSource(sourceId, kind) {
    const source = document.getElementById(sourceId);
    if (!source) return "";
    try {
      const parsed = JSON.parse(source.textContent || "null");
      if (kind === "text") return typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2);
      return JSON.stringify(parsed, null, 2);
    } catch {
      return source.textContent || "";
    }
  }

  function openRawModal(button) {
    if (!modal || !rawOutput || !modalTitle) return;
    const title = button.dataset.rawTitle || "Raw data";
    const kind = button.dataset.rawKind || "json";
    const sourceId = button.dataset.rawSource || "";
    const text = modalTextFromSource(sourceId, kind);
    modalTitle.textContent = title;
    rawOutput.textContent = text;
    rawDownloadName = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + (kind === "text" ? ".txt" : ".json");
    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeRawModal() {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  document.querySelectorAll("[data-raw-open]").forEach((button) => {
    button.addEventListener("click", () => openRawModal(button));
  });

  document.querySelectorAll("[data-raw-close]").forEach((button) => {
    button.addEventListener("click", closeRawModal);
  });

  if (modal) {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeRawModal();
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeRawModal();
      setFilters(false);
      setMobileSidebar(false);
      if (notificationPopout) notificationPopout.hidden = true;
    }
  });

  const copyButton = document.querySelector("[data-raw-copy]");
  if (copyButton) {
    copyButton.addEventListener("click", async () => {
      if (!rawOutput) return;
      await navigator.clipboard.writeText(rawOutput.textContent || "");
      copyButton.textContent = "Copied";
      window.setTimeout(() => {
        copyButton.textContent = "Copy";
      }, 1200);
    });
  }

  const downloadButton = document.querySelector("[data-raw-download]");
  if (downloadButton) {
    downloadButton.addEventListener("click", () => {
      if (!rawOutput) return;
      const blob = new Blob([rawOutput.textContent || ""], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = rawDownloadName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    });
  }

  document.querySelectorAll("[data-upload-console]").forEach((consoleRoot) => {
    const output = consoleRoot.querySelector("[data-upload-output]");
    const argsInput = consoleRoot.querySelector("[data-upload-args]");
    const executeButton = consoleRoot.querySelector("[data-upload-execute]");
    const autorunButton = consoleRoot.querySelector("[data-upload-autorun]");
    const queueButton = consoleRoot.querySelector("[data-upload-queue]");
    const clearButton = consoleRoot.querySelector("[data-upload-clear]");
    const killButton = consoleRoot.querySelector("[data-upload-kill]");
    const inputForm = consoleRoot.querySelector("[data-upload-input-form]");
    const inputField = consoleRoot.querySelector("[data-upload-input]");
    const sendButton = consoleRoot.querySelector("[data-upload-send]");
    const stateBadge = consoleRoot.querySelector("[data-upload-state]");
    const latestButton = consoleRoot.querySelector("[data-upload-latest]");
    const canExecute = consoleRoot.dataset.canExecute === "true";
    const canQueue = consoleRoot.dataset.canQueue === "true";
    let sessionId = consoleRoot.dataset.activeSession || "";
    let streamController = null;
    let followingLatest = true;
    let running = Boolean(sessionId);

    if (!output || !argsInput || !executeButton || !clearButton || !killButton || !inputForm || !inputField || !sendButton) {
      return;
    }

    const isNearBottom = () => output.scrollHeight - output.scrollTop - output.clientHeight < 28;
    const scrollLatest = () => {
      output.scrollTop = output.scrollHeight;
      followingLatest = true;
      if (latestButton) latestButton.hidden = true;
    };
    const syncScroll = () => {
      if (followingLatest) {
        window.requestAnimationFrame(scrollLatest);
      } else if (latestButton) {
        latestButton.hidden = false;
      }
    };
    const escapeHtml = (value) => String(value || "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
    const sanitizeFragment = (html) => {
      const wrapper = document.createElement("div");
      wrapper.innerHTML = String(html || "");
      wrapper.querySelectorAll("script, style, iframe, object, embed").forEach((node) => node.remove());
      wrapper.querySelectorAll("*").forEach((node) => {
        Array.from(node.attributes).forEach((attr) => {
          if (/^on/i.test(attr.name) || /javascript:/i.test(attr.value)) {
            node.removeAttribute(attr.name);
          }
        });
      });
      return wrapper;
    };
    const appendNode = (node) => {
      followingLatest = followingLatest && isNearBottom();
      output.appendChild(node);
      syncScroll();
    };
    const appendLine = (text, kind = "system") => {
      const line = document.createElement("div");
      line.className = `console-line ${kind}`;
      line.innerHTML = escapeHtml(text);
      appendNode(line);
    };
    let lastFullSnapshotText = "";
    const snapshotText = (html) => sanitizeFragment(html).textContent.replace(/\s+/g, " ").trim();
    const appendHtml = (html, replace = false) => {
      if (!replace) {
        appendNode(sanitizeFragment(html));
        return;
      }
      const text = snapshotText(html);
      if (!text || text === lastFullSnapshotText) return;
      if (lastFullSnapshotText && text.startsWith(lastFullSnapshotText)) {
        const delta = text.slice(lastFullSnapshotText.length).trim();
        if (delta) appendLine(delta, "system");
      } else {
        appendNode(sanitizeFragment(html));
      }
      lastFullSnapshotText = text;
    };
    const setRunning = (value, label) => {
      running = value;
      executeButton.disabled = value || !canExecute;
      if (autorunButton) autorunButton.disabled = value || !canExecute;
      if (queueButton) queueButton.disabled = !canQueue;
      clearButton.hidden = value;
      killButton.hidden = !value;
      inputField.disabled = !value;
      sendButton.disabled = !value;
      if (stateBadge) {
        stateBadge.textContent = label || (value ? "Running" : "Idle");
        stateBadge.classList.toggle("running", value);
        stateBadge.classList.toggle("idle", !value);
        stateBadge.classList.remove("busy");
      }
    };
    const processEvent = (payload) => {
      if (!payload || payload.type === "keepalive") return;
      if (payload.type === "html" || payload.type === "html_full") {
        appendHtml(payload.data || "", payload.type === "html_full");
        return;
      }
      if (payload.type === "exit") {
        appendLine(`Process exited with code ${payload.code}`, "system");
        setRunning(false, "Idle");
        sessionId = "";
        return;
      }
      appendLine(payload.data || payload.message || payload.error || "", payload.type === "error" ? "error" : "system");
    };
    const consumeStream = async (response) => {
      sessionId = response.headers.get("X-UA-Session-ID") || sessionId;
      const reader = response.body && response.body.getReader ? response.body.getReader() : null;
      if (!reader) {
        appendLine("Upload Assistant stream did not return a readable body.", "error");
        return;
      }
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";
        parts.forEach((line) => {
          if (!line.startsWith("data: ")) return;
          try {
            processEvent(JSON.parse(line.slice(6)));
          } catch {
            appendLine(line.slice(6), "system");
          }
        });
      }
      if (buffer.startsWith("data: ")) {
        try {
          processEvent(JSON.parse(buffer.slice(6)));
        } catch {
          appendLine(buffer.slice(6), "system");
        }
      }
    };
    const openStream = async (url, options = {}) => {
      try {
        streamController = new AbortController();
        const response = await fetch(url, { ...options, signal: streamController.signal });
        if (!response.ok) {
          const text = await response.text();
          let message = text || `Request failed with ${response.status}`;
          try {
            message = JSON.parse(text).error || message;
          } catch {}
          appendLine(message, "error");
          setRunning(false, "Idle");
          return;
        }
        await consumeStream(response);
      } catch (error) {
        if (!streamController || !streamController.signal.aborted) {
          appendLine(error.message || String(error), "error");
        }
      } finally {
        streamController = null;
        setRunning(false, "Idle");
      }
    };
    const withUnattendedArg = (value) => {
      const trimmed = String(value || "").trim();
      if (/(^|\s)--unattended(\s|$)/.test(trimmed)) return trimmed;
      return `${trimmed} --unattended`.trim();
    };
    const startUpload = (args) => {
      if (!canExecute || running) return;
      output.innerHTML = "";
      lastFullSnapshotText = "";
      appendLine("Starting Upload Assistant...");
      setRunning(true, "Running");
      openStream(consoleRoot.dataset.executeUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ args }),
      });
    };

    output.addEventListener("scroll", () => {
      followingLatest = isNearBottom();
      if (latestButton) latestButton.hidden = followingLatest;
    });
    if (latestButton) {
      latestButton.addEventListener("click", scrollLatest);
    }

    executeButton.addEventListener("click", () => {
      startUpload(argsInput.value || "");
    });

    if (autorunButton) {
      autorunButton.addEventListener("click", () => {
        argsInput.value = withUnattendedArg(argsInput.value);
        startUpload(argsInput.value || "");
      });
    }

    if (queueButton) {
      queueButton.addEventListener("click", async () => {
        if (!canQueue) return;
        argsInput.value = withUnattendedArg(argsInput.value);
        queueButton.disabled = true;
        try {
          const response = await fetch(consoleRoot.dataset.queueUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ args: argsInput.value || "" }),
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) {
            appendLine(payload.error || `Queue failed with ${response.status}`, "error");
            return;
          }
          appendLine(`Queued unattended import #${payload.id}.`, "system");
        } catch (error) {
          appendLine(error.message || String(error), "error");
        } finally {
          queueButton.disabled = !canQueue;
        }
      });
    }

    clearButton.addEventListener("click", () => {
      if (running) return;
      output.innerHTML = "";
      lastFullSnapshotText = "";
      appendLine("Upload Assistant console ready.");
      scrollLatest();
    });

    killButton.addEventListener("click", async () => {
      if (!running) return;
      try {
        await fetch(consoleRoot.dataset.killUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId }),
        });
      } catch (error) {
        appendLine(error.message || String(error), "error");
      }
      if (streamController) streamController.abort();
      output.innerHTML = "";
      lastFullSnapshotText = "";
      appendLine("Upload Assistant console ready.");
      sessionId = "";
      setRunning(false, "Idle");
      scrollLatest();
    });

    inputForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!running || !inputField.value) return;
      const value = inputField.value;
      inputField.value = "";
      appendLine(`> ${value}`, "input");
      try {
        const response = await fetch(consoleRoot.dataset.inputUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, input: value }),
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          appendLine(payload.error || `Input failed with ${response.status}`, "error");
        }
      } catch (error) {
        appendLine(error.message || String(error), "error");
      }
    });

    if (sessionId) {
      output.innerHTML = "";
      lastFullSnapshotText = "";
      appendLine("Reattaching to active Upload Assistant session...");
      setRunning(true, "Running");
      const url = `${consoleRoot.dataset.streamUrl}?session_id=${encodeURIComponent(sessionId)}`;
      openStream(url, { method: "GET" });
    } else {
      setRunning(false, stateBadge ? stateBadge.textContent : "Idle");
    }
  });

  function setText(selector, text) {
    document.querySelectorAll(selector).forEach((node) => {
      node.textContent = text;
    });
  }

  function refreshStatus() {
    if (!document.querySelector("[data-count-view], [data-queue-field], [data-service-running]")) return;
    fetch("/api/status", { headers: { accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (!payload) return;
        const counts = payload.counts || {};
        const service = payload.service || {};
        const queue = service.queue || {};
        const imports = service.imports || {};
        const viewCounts = {
          active: queue.active || 0,
          candidates: counts.candidate || 0,
          covered: counts.covered || 0,
          blocked: counts.blocked || 0,
          manual: counts.manual_review || 0,
          errors: counts.error || 0,
          baseline: counts.baseline || 0,
          inventory: counts.inventory || 0,
          ignored: counts.ignored || 0,
          imports: imports.active || 0,
          all: Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0),
        };
        Object.entries(viewCounts).forEach(([view, count]) => {
          setText(`[data-count-view="${view}"]`, String(count));
        });
        setText("[data-service-running]", service.running ? "Running" : "Stopped");
        setText('[data-queue-field="running_jobs"]', String(service.running_jobs || 0));
        setText('[data-queue-field="active"]', String(queue.active || 0));
        const maintenance = service.maintenance || {};
        const uaExecution = service.ua_execution || {};
        const footer = maintenance.active ? "Paused" : ((uaExecution.busy || service.running_jobs || queue.active || imports.active) ? "Running" : "Ready");
        setText("[data-service-footer]", footer);
        document.querySelectorAll("[data-service-footer-dot]").forEach((node) => {
          node.classList.toggle("ok", footer === "Ready");
          node.classList.toggle("warn", footer === "Paused");
          node.classList.toggle("run", footer === "Running");
        });
      })
      .catch(() => {});
  }

  window.setInterval(refreshStatus, 30000);
})();
