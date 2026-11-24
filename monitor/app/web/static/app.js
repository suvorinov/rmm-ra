(() => {
  "use strict";

  const filter = document.querySelector("[data-filter]");
  const status = document.querySelector("[data-status-filter]");
  const rows = Array.from(document.querySelectorAll("tr[data-search]"));
  const counter = document.querySelector("[data-count]");

  function applyFilter() {
    const query = (filter?.value || "").trim().toLowerCase();
    const wanted = status?.value || "";
    let visible = 0;
    for (const row of rows) {
      const matchesQuery = !query || (row.dataset.search || "").includes(query);
      const matchesStatus = !wanted || row.dataset.status === wanted;
      const show = matchesQuery && matchesStatus;
      row.hidden = !show;
      if (show) visible += 1;
    }
    if (counter) counter.textContent = String(visible);
  }

  filter?.addEventListener("input", applyFilter);
  status?.addEventListener("change", applyFilter);
  applyFilter();

  const auto = document.querySelector("[data-auto-refresh]");
  const AUTO_KEY = "rmm-monitor-autorefresh";
  if (auto) {
    auto.checked = localStorage.getItem(AUTO_KEY) === "1";
    auto.addEventListener("change", () => {
      localStorage.setItem(AUTO_KEY, auto.checked ? "1" : "0");
    });
    window.setInterval(() => {
      if (auto.checked) window.location.reload();
    }, 60000);
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.getAttribute("data-copy") || "";
      try {
        await navigator.clipboard.writeText(value);
        const previous = button.textContent;
        button.textContent = "скопировано";
        window.setTimeout(() => { button.textContent = previous; }, 1500);
      } catch (_error) {
        window.prompt("Скопируй вручную:", value);
      }
    });
  });

  const modal = document.getElementById("host-modal");
  if (modal) {
    const modalBody = modal.querySelector("[data-modal-body]");
    const modalTitle = modal.querySelector("#host-modal-title");
    const modalSub = modal.querySelector("[data-modal-sub]");
    const openPage = modal.querySelector("[data-modal-open-page]");
    const tz = document.body.dataset.tz || "Europe/Moscow";
    let lastFocus = null;
    let requestId = 0;

    function node(tag, className, text) {
      const item = document.createElement(tag);
      if (className) item.className = className;
      if (text !== undefined && text !== null) item.textContent = String(text);
      return item;
    }

    function pct(value) {
      return value === null || value === undefined ? "—" : `${Number(value).toFixed(1)}%`;
    }

    function mb(value) {
      if (value === null || value === undefined) return "—";
      const amount = Number(value);
      return amount >= 1024 ? `${(amount / 1024).toFixed(1)} ГБ` : `${amount.toFixed(0)} МБ`;
    }

    function duration(value) {
      if (value === null || value === undefined) return "—";
      let left = Math.max(0, Math.floor(Number(value)));
      const days = Math.floor(left / 86400); left -= days * 86400;
      const hours = Math.floor(left / 3600); left -= hours * 3600;
      const minutes = Math.floor(left / 60);
      const parts = [];
      if (days) parts.push(`${days} д`);
      if (hours) parts.push(`${hours} ч`);
      if (minutes && parts.length < 2) parts.push(`${minutes} мин`);
      if (!parts.length) parts.push(`${left} с`);
      return parts.join(" ");
    }

    const timeFormat = new Intl.DateTimeFormat("ru-RU", {
      timeZone: tz, day: "2-digit", month: "2-digit", year: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });

    function localTime(iso) {
      if (!iso) return "—";
      const date = new Date(iso);
      return Number.isNaN(date.getTime()) ? "—" : timeFormat.format(date);
    }

    function ago(iso) {
      if (!iso) return "—";
      const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
      if (!Number.isFinite(seconds) || seconds < 0) return "только что";
      if (seconds < 60) return `${Math.floor(seconds)} с назад`;
      if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
      if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч назад`;
      return `${Math.floor(seconds / 86400)} д назад`;
    }

    function chips(values) {
      const wrap = node("div", "modal-chips");
      const list = Array.isArray(values) ? values.filter(Boolean) : [];
      if (!list.length) {
        wrap.append(node("span", "muted", "—"));
        return wrap;
      }
      list.forEach((value) => wrap.append(node("span", "chip", value)));
      return wrap;
    }

    function makeTable(headers, rows, numeric = []) {
      const wrap = node("div", "table-wrap");
      const table = node("table", "table compact");
      const head = node("thead");
      const headRow = node("tr");
      headers.forEach((title, index) => headRow.append(node("th", numeric.includes(index) ? "num" : "", title)));
      head.append(headRow);
      table.append(head);
      const body = node("tbody");
      rows.forEach((cells) => {
        const row = node("tr");
        cells.forEach((value, index) => {
          const cell = node("td", numeric.includes(index) ? "num" : "");
          if (value instanceof Node) cell.append(value);
          else cell.textContent = value === null || value === undefined ? "—" : String(value);
          row.append(cell);
        });
        body.append(row);
      });
      table.append(body);
      wrap.append(table);
      return wrap;
    }

    function section(title) {
      const block = node("section", "modal-section");
      block.append(node("h3", "", title));
      return block;
    }

    function render(data) {
      const metrics = data.metrics || {};
      const snap = data.snapshot;
      modalTitle.textContent = data.name || data.hostname || "Хост";
      modalSub.textContent = [data.hostname, data.ip, data.group, data.os].filter(Boolean).join(" · ");

      const content = document.createDocumentFragment();
      const badges = node("div", "badges");
      badges.append(node("span", `badge ${data.online ? "badge-online" : "badge-offline"}`, data.online ? "в сети" : "офлайн"));
      if (data.last_snapshot_status === "error") badges.append(node("span", "badge badge-bad", "ошибка снимка"));
      else if (data.last_snapshot_status === "ok") badges.append(node("span", "badge badge-ok", "снимок ok"));
      content.append(badges);

      const stats = node("div", "modal-stats");
      [
        ["CPU", pct(metrics.cpu_percent)],
        ["RAM", pct(metrics.mem_percent)],
        ["Диск", pct(metrics.disk_max_percent)],
        ["Аптайм", duration(metrics.uptime_s)],
        ["Процессы", metrics.process_count ?? "—"],
        ["Браузеры", `${metrics.browser_kinds || 0} / ${metrics.browser_processes || 0}`],
      ].forEach(([label, value]) => {
        const cell = node("div", "modal-stat");
        cell.append(node("span", "", label), node("b", "", value));
        stats.append(cell);
      });
      content.append(stats);

      const kv = node("dl", "kv");
      const addKv = (key, value) => {
        kv.append(node("dt", "", key));
        const dd = node("dd");
        if (value instanceof Node) dd.append(value);
        else dd.textContent = value === null || value === undefined ? "—" : String(value);
        kv.append(dd);
      };
      addKv("Группа", data.group || "—");
      addKv("ОС", data.os_caption || data.os || "—");
      addKv("CPU", metrics.cpu_model || "—");
      addKv("Ядра / потоки", `${metrics.cpu_cores || "?"} / ${metrics.cpu_logical || "?"}`);
      addKv("Агент", data.agent_version || "—");
      addKv("Виден в сети", localTime(data.last_seen_online));
      addKv("Инвентарь", localTime(data.last_inventory_at));
      addKv("Последний снимок", snap ? localTime(snap.taken_at) : "—");
      const common = section("Общая информация");
      common.append(kv);
      content.append(common);

      if (data.last_snapshot_status === "error" && data.last_snapshot_error) {
        content.append(node("div", "modal-error",
          `Последняя попытка снимка не удалась (${ago(data.last_snapshot_attempt_at)}): ${data.last_snapshot_error}`));
      }

      if (!snap) {
        content.append(node("p", "muted", "Снимков пока нет — метрики появятся после первого опроса."));
      } else {
        const disks = section("Диски");
        if (snap.disks && snap.disks.length) {
          disks.append(makeTable(
            ["Диск", "Занято", "Свободно"],
            snap.disks.map((disk) => [`${disk.mount} ${disk.label || ""}`.trim(), pct(disk.used_pct), `${Number(disk.free_gb).toFixed(1)} ГБ`]),
            [1, 2],
          ));
        } else {
          disks.append(node("p", "muted", "Данных о дисках нет."));
        }
        content.append(disks);

        const browsers = section("Браузеры");
        if (snap.browsers && snap.browsers.length) {
          browsers.append(makeTable(
            ["Браузер", "Процессы", "Память", "Пользователи"],
            snap.browsers.map((browser) => [browser.name, browser.processes, mb(browser.memory_mb), chips(browser.users)]),
            [1, 2],
          ));
        } else {
          browsers.append(node("p", "muted", "Браузерные процессы не обнаружены."));
        }
        content.append(browsers);

        const processes = section("Процессы · топ по памяти");
        if (snap.processes && snap.processes.length) {
          processes.append(makeTable(
            ["Процесс", "Экз.", "Память", "CPU-время"],
            snap.processes.map((process) => [process.name, process.count, mb(process.ws_mb), `${Number(process.cpu_s).toFixed(1)} с`]),
            [1, 2, 3],
          ));
        } else {
          processes.append(node("p", "muted", "Данных о процессах нет."));
        }
        content.append(processes);

        const people = section("Пользователи и сеть");
        const peopleKv = node("dl", "kv");
        peopleKv.append(node("dt", "", "Консольный пользователь"));
        peopleKv.append(node("dd", "", snap.console_user || metrics.console_user || "—"));
        peopleKv.append(node("dt", "", "Активные сессии"));
        const sessions = node("dd"); sessions.append(chips(snap.users)); peopleKv.append(sessions);
        peopleKv.append(node("dt", "", "IP-адреса"));
        const addresses = node("dd"); addresses.append(chips(snap.network && snap.network.addresses)); peopleKv.append(addresses);
        people.append(peopleKv);
        content.append(people);
      }

      modalBody.replaceChildren(content);
    }

    async function open(hostId) {
      lastFocus = document.activeElement;
      modalTitle.textContent = "Хост";
      modalSub.textContent = "";
      openPage.href = "/host/" + encodeURIComponent(hostId);
      modalBody.replaceChildren(node("p", "muted", "Загрузка…"));
      modal.hidden = false;
      const current = ++requestId;
      try {
        const response = await fetch("/api/hosts/" + encodeURIComponent(hostId), { credentials: "same-origin" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (current === requestId) render(data);
      } catch (error) {
        if (current === requestId) {
          modalBody.replaceChildren(node("p", "notice-bad", `Не удалось загрузить данные: ${error.message}`));
        }
      }
    }

    function close() {
      modal.hidden = true;
      requestId += 1;
      if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    }

    document.querySelectorAll("tr[data-host-id]").forEach((row) => {
      row.tabIndex = 0;
      row.setAttribute("aria-label", "Открыть детали хоста");
      row.addEventListener("click", (event) => {
        if (event.target.closest("a, button, form, input, select, textarea")) return;
        open(row.dataset.hostId || "");
      });
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open(row.dataset.hostId || "");
        }
      });
    });

    modal.querySelectorAll("[data-modal-close]").forEach((button) => {
      button.addEventListener("click", close);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hidden) close();
    });
  }
})();
