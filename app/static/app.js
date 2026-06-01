(function () {
  const shell = document.querySelector("[data-app-shell]");
  const storage = window.localStorage;

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
        const viewCounts = {
          active: queue.active || 0,
          candidates: counts.candidate || 0,
          blocked: counts.blocked || 0,
          manual: counts.manual_review || 0,
          errors: counts.error || 0,
          baseline: counts.baseline || 0,
          inventory: counts.inventory || 0,
          ignored: counts.ignored || 0,
          all: Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0),
        };
        Object.entries(viewCounts).forEach(([view, count]) => {
          setText(`[data-count-view="${view}"]`, String(count));
        });
        setText("[data-service-running]", service.running ? "Running" : "Stopped");
        setText('[data-queue-field="running_jobs"]', String(service.running_jobs || 0));
        setText('[data-queue-field="active"]', String(queue.active || 0));
      })
      .catch(() => {});
  }

  window.setInterval(refreshStatus, 30000);
})();
