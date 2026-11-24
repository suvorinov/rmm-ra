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
})();
