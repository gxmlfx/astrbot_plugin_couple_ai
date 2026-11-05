(() => {
  const api = () => window.AstrBotPluginPage;
  const $ = (id) => document.getElementById(id);
  const esc = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const state = { tasks: [], events: [], diaries: [], counts: {} };

  function setMsg(text, kind = "") {
    const el = $("form-msg");
    el.textContent = text || "";
    el.className = "msg" + (kind ? " " + kind : "");
  }

  function badge(status) {
    return `<span class="badge ${esc(status)}">${esc(status)}</span>`;
  }

  function renderStats() {
    $("stat-pending").textContent = state.counts.pending ?? 0;
    $("stat-sent").textContent = state.counts.sent ?? 0;
    $("stat-done").textContent = state.counts.done ?? 0;
    $("stat-events").textContent = state.counts.events ?? 0;
    $("stat-diaries").textContent = state.counts.diaries ?? 0;
  }

  function renderEvents() {
    const root = $("events");
    if (!state.events.length) {
      root.innerHTML = `<li class="hint">还没有事件。Bot 在任意会话发言后会出现在这里。</li>`;
      return;
    }
    root.innerHTML = state.events
      .slice()
      .reverse()
      .map((e) => {
        const isUser = e.role === "user";
        const role = isUser ? "用户" : "我";
        const kind = e.kind_label || e.kind || "";
        return `<li><span class="meta">${esc(e.time_text)} · ${esc(e.label)} · <span class="role ${isUser ? "user" : "bot"}">${role}</span> · ${esc(kind)}</span>${esc(e.text)}</li>`;
      })
      .join("");
  }

  function renderTasks() {
    const root = $("tasks");
    if (!state.tasks.length) {
      root.innerHTML = `<tr><td colspan="8" class="hint">还没有任务。用左边的表单创建第一条。</td></tr>`;
      return;
    }
    root.innerHTML = state.tasks
      .map((t) => {
        const ops = [];
        if (t.status === "pending" || t.status === "sent") {
          ops.push(`<button class="mini" data-act="cancel" data-id="${esc(t.id)}">取消</button>`);
        }
        if (t.status === "failed" || t.status === "cancelled") {
          ops.push(`<button class="mini" data-act="retry" data-id="${esc(t.id)}">重试</button>`);
        }
        if (t.status !== "done") {
          ops.push(`<button class="mini" data-act="done" data-id="${esc(t.id)}">完成</button>`);
        }
        return `<tr>
          <td><code>${esc(t.id)}</code></td>
          <td>${esc(t.from_label)}</td>
          <td>${esc(t.to_label)}</td>
          <td class="text">${esc(t.text)}</td>
          <td>${esc(t.due_text)}</td>
          <td>${esc(t.attempts ?? 0)}</td>
          <td>${badge(t.status)}${t.error ? `<div class="hint">${esc(t.error)}</div>` : ""}</td>
          <td><div class="ops">${ops.join("")}</div></td>
        </tr>`;
      })
      .join("");
  }

  function renderDiaries() {
    const root = $("diaries");
    if (!state.diaries.length) {
      root.innerHTML = `<li class="hint">还没有日记。点右上角生成今天的。</li>`;
      return;
    }
    root.innerHTML = state.diaries
      .map(
        (d) => `<li data-date="${esc(d.date)}"><span class="meta">${esc(d.date)} · ${esc(d.size ?? 0)} 字</span>${esc(d.date)}</li>`
      )
      .join("");
  }

  function render() {
    renderStats();
    renderEvents();
    renderDiaries();
    renderTasks();
  }

  async function load() {
    if (!api()) {
      setMsg("插件页面桥接未加载，请刷新。", "err");
      return;
    }
    try {
      const data = await api().apiGet("overview");
      state.tasks = data.tasks || [];
      state.events = data.events || [];
      state.diaries = data.diaries || [];
      state.counts = data.counts || {};
      render();
    } catch (error) {
      setMsg("加载失败：" + (error?.message || error), "err");
    }
  }

  async function post(endpoint, body) {
    return api().apiPost(endpoint, body || {});
  }

  $("task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const due = $("due_at").value;
    const body = {
      to_session: $("to_session").value.trim(),
      text: $("text").value.trim(),
      due_at: due || "",
      kind: $("kind").value,
    };
    if (!body.to_session || !body.text) {
      setMsg("目标会话和内容都要填。", "err");
      return;
    }
    setMsg("创建中…");
    try {
      const result = await post("task/create", body);
      if (!result.ok) throw new Error(result.error || "创建失败");
      setMsg("已创建：" + result.task.id, "ok");
      $("text").value = "";
      await load();
    } catch (error) {
      setMsg(error?.message || String(error), "err");
    }
  });

  $("tasks").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-act]");
    if (!button) return;
    const { act, id } = button.dataset;
    button.disabled = true;
    try {
      const result = await post("task/" + act, { id });
      if (!result.ok) throw new Error(result.error || "操作失败");
      await load();
    } catch (error) {
      setMsg(error?.message || String(error), "err");
    } finally {
      button.disabled = false;
    }
  });

  $("diaries").addEventListener("click", async (event) => {
    const item = event.target.closest("li[data-date]");
    if (!item) return;
    try {
      const data = await api().apiGet("diary/read", { date: item.dataset.date });
      $("diary-preview").textContent = data.diary?.content || "（空）";
    } catch (error) {
      setMsg(error?.message || String(error), "err");
    }
  });

  $("diary-generate").addEventListener("click", async () => {
    const button = $("diary-generate");
    button.disabled = true;
    setMsg("日记生成中…");
    try {
      const result = await api().apiPost("diary/generate", { send: true });
      if (!result.ok) throw new Error(result.error || "生成失败");
      $("diary-preview").textContent = result.content || "";
      setMsg("日记已生成并发送", "ok");
      await load();
    } catch (error) {
      setMsg(error?.message || String(error), "err");
    } finally {
      button.disabled = false;
    }
  });

  $("diary-regenerate").addEventListener("click", async () => {
    if (!confirm("用当前事件重写所有已有日记？原日记会被覆盖。")) return;
    const button = $("diary-regenerate");
    button.disabled = true;
    setMsg("日记重写中…");
    try {
      const result = await api().apiPost("diary/regenerate", {});
      if (!result.ok) throw new Error(result.error || "重写失败");
      const done = (result.results || []).filter((x) => x.ok).length;
      setMsg(`已重写 ${done} 篇日记`, "ok");
      await load();
    } catch (error) {
      setMsg(error?.message || String(error), "err");
    } finally {
      button.disabled = false;
    }
  });

  $("diary-refresh").addEventListener("click", load);

  $("clear-events").addEventListener("click", async () => {
    if (!confirm("清空全部事件？")) return;
    try {
      await post("event/clear", {});
      await load();
    } catch (error) {
      setMsg(error?.message || String(error), "err");
    }
  });

  $("refresh").addEventListener("click", load);

  function setConfigMsg(text, kind = "") {
    const el = $("config-msg");
    el.textContent = text || "";
    el.className = "msg" + (kind ? " " + kind : "");
  }

  function fillConfig(c) {
    if (!c) return;
    const set = (id, value) => { const el = $(id); if (el) el.value = value ?? ""; };
    const check = (id, value) => { const el = $(id); if (el) el.checked = !!value; };
    check("cfg-enabled", c.enabled);
    check("cfg-restrict", c.restrict_sessions);
    check("cfg-disable-external", c.disable_external_relay_tools);
    check("cfg-event-log", c.event_log_enabled);
    check("cfg-diary", c.diary_enabled);
    set("cfg-platform", c.platform_id);
    set("cfg-owner-qq", c.owner_qq);
    set("cfg-partner-qq", c.partner_qq);
    set("cfg-group-id", c.group_id);
    set("cfg-owner-name", c.owner_name);
    set("cfg-partner-name", c.partner_name);
    set("cfg-group-name", c.group_name);
    set("cfg-inject", c.event_inject_count);
    set("cfg-event-chars", c.event_max_chars);
    set("cfg-event-keep", c.event_keep);
    set("cfg-interval", c.check_interval_seconds);
    set("cfg-task-pending", c.task_max_pending);
    set("cfg-task-chars", c.task_max_chars);
    set("cfg-prefix", c.deliver_prefix);
    set("cfg-diary-time", c.diary_time);
    set("cfg-diary-target", c.diary_target);
  }

  async function loadConfig() {
    if (!api()) return;
    try {
      const data = await api().apiGet("config");
      fillConfig(data.config);
    } catch (error) {
      setConfigMsg("设置加载失败：" + (error?.message || error), "err");
    }
  }

  $("config-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = {
      enabled: $("cfg-enabled").checked,
      restrict_sessions: $("cfg-restrict").checked,
      disable_external_relay_tools: $("cfg-disable-external").checked,
      event_log_enabled: $("cfg-event-log").checked,
      diary_enabled: $("cfg-diary").checked,
      platform_id: $("cfg-platform").value.trim(),
      owner_qq: $("cfg-owner-qq").value.trim(),
      partner_qq: $("cfg-partner-qq").value.trim(),
      group_id: $("cfg-group-id").value.trim(),
      owner_name: $("cfg-owner-name").value.trim(),
      partner_name: $("cfg-partner-name").value.trim(),
      group_name: $("cfg-group-name").value.trim(),
      event_inject_count: $("cfg-inject").value,
      event_max_chars: $("cfg-event-chars").value,
      event_keep: $("cfg-event-keep").value,
      check_interval_seconds: $("cfg-interval").value,
      task_max_pending: $("cfg-task-pending").value,
      task_max_chars: $("cfg-task-chars").value,
      deliver_prefix: $("cfg-prefix").value,
      diary_time: $("cfg-diary-time").value.trim(),
      diary_target: $("cfg-diary-target").value.trim(),
    };
    setConfigMsg("保存中…");
    try {
      const result = await api().apiPost("config/save", body);
      if (!result.ok) throw new Error(result.error || "保存失败");
      fillConfig(result.config);
      setConfigMsg("已保存", "ok");
    } catch (error) {
      setConfigMsg(error?.message || String(error), "err");
    }
  });

  async function waitForBridge(timeoutMs = 5000) {
    const started = Date.now();
    while (!window.AstrBotPluginPage && Date.now() - started < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return window.AstrBotPluginPage;
  }

  async function boot() {
    const bridge = await waitForBridge();
    if (!bridge) {
      setMsg("AstrBot 插件页面桥接未加载，请刷新页面。", "err");
      return;
    }
    await bridge.ready();
    await loadConfig();
    await load();
    setInterval(load, 5000);
  }

  boot();
})();
