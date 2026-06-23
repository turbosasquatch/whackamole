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

  function setButtonTick(button, label = "Done") {
    if (!button) return;
    button.classList.add("button-ticked");
    button.setAttribute("aria-label", label);
    button.innerHTML = '<span class="submit-tick" aria-hidden="true">&#10003;</span><span class="sr-only">' + label + "</span>";
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  const localDateTimeFormatter = (() => {
    try {
      if (!window.Intl || !Intl.DateTimeFormat) return null;
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
    } catch {
      return null;
    }
  })();

  function formatLocalDateTimes(root = document) {
    if (!localDateTimeFormatter || !root.querySelectorAll) return;
    root.querySelectorAll("[data-local-datetime]").forEach((node) => {
      const value = node.getAttribute("datetime");
      if (!value) return;
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return;
      const fallback = node.textContent || "";
      node.textContent = localDateTimeFormatter.format(parsed);
      if (fallback && !node.title) {
        node.title = fallback;
      }
    });
  }

  formatLocalDateTimes();

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

  const searchModal = document.querySelector("[data-search-modal]");
  const searchInput = document.querySelector("#mobile-search-input");
  function setSearchModal(open) {
    if (!searchModal) return;
    searchModal.hidden = !open;
    document.body.classList.toggle("modal-open", open);
    if (open && searchInput) {
      setTimeout(() => searchInput.focus(), 0);
    }
  }

  document.querySelectorAll("[data-search-open]").forEach((button) => {
    button.addEventListener("click", () => setSearchModal(true));
  });

  document.querySelectorAll("[data-search-close]").forEach((button) => {
    button.addEventListener("click", () => setSearchModal(false));
  });

  if (searchModal) {
    searchModal.addEventListener("click", (event) => {
      if (event.target === searchModal) setSearchModal(false);
    });
  }

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

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const message = form.dataset.confirm || "Continue with this action?";
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    });
  });

  document.querySelectorAll("form[data-submit-tick]").forEach((form) => {
    form.addEventListener("submit", () => {
      if (form.matches("[data-queue-upload-form], [data-ignore-item-form]")) return;
      const button = form.querySelector("[data-submit-tick-button]") || form.querySelector('button[type="submit"]');
      setButtonTick(button, form.dataset.submitTick || "Done");
    });
  });

  document.querySelectorAll("form[data-queue-upload-form]").forEach((form) => {
    const initialButton = form.querySelector("[data-submit-tick-button]") || form.querySelector('button[type="submit"]');
    if (initialButton && !initialButton.dataset.originalLabel) {
      initialButton.dataset.originalLabel = initialButton.textContent.trim();
    }
    if (form.dataset.queuedImportId && initialButton) {
      setButtonTick(initialButton, form.dataset.submitTick || "Upload queued");
      initialButton.disabled = true;
    }
    form.addEventListener("submit", async (event) => {
      const queueUrl = form.dataset.queueUrl;
      const button = form.querySelector("[data-submit-tick-button]") || form.querySelector('button[type="submit"]');
      if (!queueUrl || !button || !window.fetch) return;
      event.preventDefault();
      const originalLabel = button.dataset.originalLabel || button.textContent.trim() || "Upload";
      button.disabled = true;
      try {
        const response = await fetch(queueUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) {
          throw new Error(payload.error || `Queue failed with ${response.status}`);
        }
        if (payload.id) {
          form.dataset.queuedImportId = String(payload.id);
        }
        setButtonTick(button, form.dataset.submitTick || "Upload queued");
      } catch (error) {
        const message = error.message || "Queue failed";
        button.disabled = false;
        button.textContent = form.dataset.submitErrorLabel || originalLabel || "Retry";
        button.title = message;
        button.setAttribute("aria-label", message);
      }
    });
  });

  document.querySelectorAll("form[data-ignore-item-form]").forEach((form) => {
    const button = form.querySelector("[data-submit-tick-button]") || form.querySelector('button[type="submit"]');
    if (button && !button.dataset.originalLabel) {
      button.dataset.originalLabel = button.textContent.trim();
    }
    form.addEventListener("submit", async (event) => {
      if (!button || !window.fetch || !window.FormData) return;
      event.preventDefault();
      const originalLabel = button.dataset.originalLabel || button.textContent.trim() || "Ignore";
      button.disabled = true;
      button.textContent = "Ignoring";
      button.removeAttribute("title");
      button.removeAttribute("aria-label");
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
        });
        if (!response.ok) {
          throw new Error(`Ignore failed with ${response.status}`);
        }
        const card = form.closest(".media-card");
        if (card) {
          card.remove();
        } else {
          setButtonTick(button, form.dataset.submitTick || "Ignored");
        }
      } catch (error) {
        const message = error.message || "Ignore failed";
        button.disabled = false;
        button.textContent = form.dataset.submitErrorLabel || originalLabel || "Retry";
        button.title = message;
        button.setAttribute("aria-label", message);
      }
    });
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
    if (!buttons.length || !panels.length) return;

    const knownTab = (name) => buttons.some((button) => button.dataset.tabTarget === name);
    const tabFromHash = () => window.location.hash.replace("#", "");
    const activate = (name, options = {}) => {
      if (!knownTab(name)) return;
      buttons.forEach((button) => {
        const active = button.dataset.tabTarget === name;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
        button.tabIndex = active ? 0 : -1;
        if (active && options.focus) {
          button.focus();
        }
      });
      panels.forEach((panel) => {
        const active = panel.dataset.tabPanel === name;
        panel.classList.toggle("active", active);
        panel.hidden = !active;
      });
      if (options.syncHash && window.history && tabFromHash() !== name) {
        window.history.pushState(null, "", `#${name}`);
      }
    };

    buttons.forEach((button) => {
      button.addEventListener("click", () => activate(button.dataset.tabTarget, { syncHash: true }));
      button.addEventListener("keydown", (event) => {
        const currentIndex = buttons.indexOf(button);
        let nextIndex = currentIndex;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          nextIndex = (currentIndex + 1) % buttons.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          nextIndex = (currentIndex - 1 + buttons.length) % buttons.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = buttons.length - 1;
        } else {
          return;
        }
        event.preventDefault();
        activate(buttons[nextIndex].dataset.tabTarget, { focus: true, syncHash: true });
      });
    });

    const requested = tabFromHash();
    if (knownTab(requested)) {
      activate(requested);
    }

    const syncFromLocation = () => {
      const requestedTab = tabFromHash();
      if (knownTab(requestedTab)) {
        activate(requestedTab);
      }
    };
    window.addEventListener("hashchange", syncFromLocation);
    window.addEventListener("popstate", syncFromLocation);
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
      setSearchModal(false);
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
    let eventSource = null;
    let followingLatest = true;
    let running = Boolean(sessionId);

    const errorMessage = (error) => error && error.message ? error.message : String(error || "Unknown error");
    const disableControls = () => {
      [argsInput, executeButton, autorunButton, queueButton, clearButton, killButton, inputField, sendButton].forEach((node) => {
        if (node) node.disabled = true;
      });
    };
    const appendInitError = (message) => {
      if (output) {
        const line = document.createElement("div");
        line.className = "console-line error";
        line.textContent = message;
        output.appendChild(line);
        return;
      }
      const warning = document.createElement("p");
      warning.className = "upload-console-warning";
      warning.textContent = message;
      consoleRoot.appendChild(warning);
    };

    const missingControls = [];
    if (!output) missingControls.push("output");
    if (!argsInput) missingControls.push("arguments input");
    if (!executeButton) missingControls.push("execute button");
    if (!clearButton) missingControls.push("clear button");
    if (!killButton) missingControls.push("kill button");
    if (!inputForm) missingControls.push("input form");
    if (!inputField) missingControls.push("input field");
    if (!sendButton) missingControls.push("send button");
    if (missingControls.length) {
      appendInitError(`Upload Assistant console could not initialize. Missing: ${missingControls.join(", ")}.`);
      disableControls();
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
    const closeEventSource = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
    };
    const processEvent = (payload) => {
      if (!payload || payload.type === "keepalive") return;
      if (payload.type === "html" || payload.type === "html_full") {
        appendHtml(payload.data || "", payload.type === "html_full");
        return;
      }
      if (payload.type === "complete") {
        appendLine(payload.data || "Upload Assistant session finished.", "system");
        setRunning(false, "Idle");
        sessionId = "";
        closeEventSource();
        return;
      }
      if (payload.type === "exit") {
        appendLine(`Process exited with code ${payload.code}`, "system");
        setRunning(false, "Idle");
        sessionId = "";
        closeEventSource();
        return;
      }
      appendLine(payload.data || payload.message || payload.error || "", payload.type === "error" ? "error" : "system");
    };
    const handleEventData = (data) => {
      try {
        processEvent(JSON.parse(data));
      } catch {
        appendLine(data, "system");
      }
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
      let receivedEvent = false;
      const handleLine = (line) => {
        if (!line.startsWith("data: ")) return;
        receivedEvent = true;
        handleEventData(line.slice(6));
      };
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";
        parts.forEach(handleLine);
      }
      if (buffer.startsWith("data: ")) {
        handleLine(buffer);
      }
      if (!receivedEvent) {
        appendLine("Upload Assistant stream ended without output.", "error");
      }
    };
    const openEventStream = (url) => {
      if (!window.EventSource) {
        openStream(url, { method: "GET" });
        return;
      }
      closeEventSource();
      let receivedEvent = false;
      eventSource = new EventSource(url);
      eventSource.onopen = () => {
        appendLine("Connected to Upload Assistant stream.", "system");
      };
      eventSource.onmessage = (event) => {
        receivedEvent = true;
        handleEventData(event.data || "");
      };
      eventSource.onerror = () => {
        closeEventSource();
        if (!running) return;
        if (!receivedEvent) {
          appendLine("Upload Assistant stream connection closed before output.", "error");
        }
        setRunning(false, "Idle");
        sessionId = "";
      };
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
          appendLine(errorMessage(error), "error");
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
    const startUpload = async (args) => {
      try {
        if (running) {
          appendLine("Upload Assistant is already running.", "system");
          return;
        }
        if (!canExecute) {
          appendLine("Upload Assistant cannot start for this item right now.", "error");
          return;
        }
        if (!consoleRoot.dataset.executeUrl) {
          appendLine("Upload Assistant execute URL is missing.", "error");
          return;
        }
        output.innerHTML = "";
        lastFullSnapshotText = "";
        appendLine("Starting Upload Assistant...");
        setRunning(true, "Running");
        const response = await fetch(consoleRoot.dataset.executeUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Upload-Console-Start-Only": "true" },
          body: JSON.stringify({ args }),
        });
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
        const payload = await response.json().catch(() => ({}));
        sessionId = response.headers.get("X-UA-Session-ID") || payload.session_id || sessionId;
        if (!sessionId) {
          appendLine("Upload Assistant did not return a session id.", "error");
          setRunning(false, "Idle");
          return;
        }
        const url = `${consoleRoot.dataset.streamUrl}?session_id=${encodeURIComponent(sessionId)}`;
        openEventStream(url);
      } catch (error) {
        appendLine(errorMessage(error), "error");
        setRunning(false, "Idle");
      }
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
          setButtonTick(queueButton, "Upload queued");
        } catch (error) {
          appendLine(errorMessage(error), "error");
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
        appendLine(errorMessage(error), "error");
      }
      closeEventSource();
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
        appendLine(errorMessage(error), "error");
      }
    });

    if (sessionId) {
      output.innerHTML = "";
      lastFullSnapshotText = "";
      appendLine("Reattaching to active Upload Assistant session...");
      setRunning(true, "Running");
      const url = `${consoleRoot.dataset.streamUrl}?session_id=${encodeURIComponent(sessionId)}`;
      openEventStream(url);
    } else {
      setRunning(false, stateBadge ? stateBadge.textContent : "Idle");
    }
  });

  function setText(selector, text) {
    document.querySelectorAll(selector).forEach((node) => {
      node.textContent = text;
    });
  }

  function formatEventTime(value) {
    const timestamp = Number(value || 0);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return "";
    return new Date(timestamp * 1000).toLocaleString();
  }

  function formatEventDateTimeIso(value) {
    const timestamp = Number(value || 0);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return "";
    return new Date(timestamp * 1000).toISOString();
  }

  function renderNotifications(events) {
    const rows = Array.isArray(events) ? events : [];
    const count = rows.length;
    document.querySelectorAll("[data-notification-count]").forEach((node) => {
      node.textContent = String(count);
      node.hidden = count === 0;
    });
    document.querySelectorAll("[data-notification-clear]").forEach((node) => {
      node.hidden = count === 0;
    });
    document.querySelectorAll("[data-notification-empty]").forEach((node) => {
      node.hidden = count !== 0;
    });
    document.querySelectorAll("[data-notification-list]").forEach((list) => {
      list.hidden = count === 0;
      list.innerHTML = rows.slice().reverse().map((event) => {
        const repeat = Number(event && event.count ? event.count : 0);
        return [
          "<li>",
          `<time datetime="${escapeHtml(formatEventDateTimeIso(event && event.last_seen_at))}" data-local-datetime>${escapeHtml(formatEventTime(event && event.last_seen_at))}</time>`,
          `<span>${escapeHtml(event && event.message)}</span>`,
          repeat > 1 ? `<em>x${escapeHtml(repeat)}</em>` : "",
          "</li>",
        ].join("");
      }).join("");
      formatLocalDateTimes(list);
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
        const reports = service.reports || {};
        const viewCounts = {
          active: queue.active || 0,
          candidates: counts.candidate || 0,
          covered: counts.covered || 0,
          rejected: counts.rejected || 0,
          blocked: counts.blocked || 0,
          skipped: counts.skipped || 0,
          manual: counts.manual_review || 0,
          errors: counts.error || 0,
          baseline: counts.baseline || 0,
          inventory: counts.inventory || 0,
          ignored: counts.ignored || 0,
          reports: reports.open || 0,
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
        const footer = maintenance.active ? "Paused" : ((uaExecution.busy || service.running_jobs || queue.active || imports.running) ? "Running" : "Ready");
        setText("[data-service-footer]", footer);
        document.querySelectorAll("[data-service-footer-dot]").forEach((node) => {
          node.classList.toggle("ok", footer === "Ready");
          node.classList.toggle("warn", footer === "Paused");
          node.classList.toggle("run", footer === "Running");
        });
        renderNotifications(service.service_errors || []);
      })
      .catch(() => {});
  }

  window.setInterval(refreshStatus, 30000);
})();
