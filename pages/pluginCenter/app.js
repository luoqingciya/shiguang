const bridge = window.AstrBotPluginPage;

const state = {
  overview: {},
  groups: [],
  selectedGroup: "",
  rankingType: "today",
  trendDays: 7,
  safety: { builtin_terms: [], custom_terms: [] },
  blacklist: [],
  thumbs: {},
  members: [],
  memberQuery: "",
  memberTotal: 0,
  memberOffset: 0,
  memberPageSize: 50,
  membersLoading: false,
  selectedMember: null,
  memberDialogTrigger: null,
  operations: {},
  subscriptions: [],
  reminders: [],
  auditLogs: [],
  auditAction: "",
  auditOperator: "",
  auditTotal: 0,
  auditOffset: 0,
  auditPageSize: 50,
  auditLoading: false,
  loading: false,
  loaded: {
    overview: false,
    groups: false,
    safety: false,
    blacklist: false,
    members: false,
    operations: false,
    audit: false,
  },
};

const MAX_BACKUP_BYTES = 5 * 1024 * 1024;
const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
const numberFormatter = new Intl.NumberFormat("zh-CN");

const $ = (id) => document.getElementById(id);
const els = {
  overview: $("overview"),
  groupList: $("groupList"),
  selectedGroupName: $("selectedGroupName"),
  selectedGroupMeta: $("selectedGroupMeta"),
  activityTrack: $("activityTrack"),
  trendCaption: $("trendCaption"),
  rankingContent: $("rankingContent"),
  memberCount: $("memberCount"),
  memberSearch: $("memberSearch"),
  memberList: $("memberList"),
  memberLoadMore: $("memberLoadMore"),
  memberDialog: $("memberDialog"),
  memberForm: $("memberForm"),
  memberDialogIdentity: $("memberDialogIdentity"),
  memberUserId: $("memberUserId"),
  memberCoins: $("memberCoins"),
  memberAffection: $("memberAffection"),
  memberTotalDays: $("memberTotalDays"),
  memberStreakDays: $("memberStreakDays"),
  memberFormError: $("memberFormError"),
  builtinTerms: $("builtinTerms"),
  builtinCount: $("builtinCount"),
  builtinSearch: $("builtinSearch"),
  customTerms: $("customTerms"),
  customCount: $("customCount"),
  blacklistContent: $("blacklistContent"),
  blacklistCount: $("blacklistCount"),
  latestBackup: $("latestBackup"),
  importFile: $("importFile"),
  importBtn: $("importBtn"),
  importResult: $("importResult"),
  toast: $("toast"),
  dialog: $("confirmDialog"),
  dialogTitle: $("dialogTitle"),
  dialogMessage: $("dialogMessage"),
  globalError: $("globalError"),
  globalErrorMessage: $("globalErrorMessage"),
  termError: $("termError"),
  blacklistError: $("blacklistError"),
  statsCards: $("statsCards"),
  subscriptionList: $("subscriptionList"),
  subscriptionCount: $("subscriptionCount"),
  reminderList: $("reminderList"),
  reminderCount: $("reminderCount"),
  seasonSettleGroup: $("seasonSettleGroup"),
  seasonSettleBtn: $("seasonSettleBtn"),
  reportBtn: $("reportBtn"),
  reportDays: $("reportDays"),
  seasonSettleResult: $("seasonSettleResult"),
  auditBody: $("auditBody"),
  auditEmpty: $("auditEmpty"),
  auditLoadMore: $("auditLoadMore"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function apiResult(result) {
  if (result?.success === false) throw new Error(result.error || "请求失败");
  return result || {};
}

function showToast(message, tone = "normal") {
  els.toast.textContent = message;
  els.toast.className = `toast show${tone === "error" ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { els.toast.className = "toast"; }, 3200);
}

function showGlobalError(messages) {
  const unique = [...new Set(messages.filter(Boolean))];
  els.globalErrorMessage.textContent = unique.length
    ? `部分数据加载失败：${unique.join("；")}`
    : "部分数据加载失败。";
  els.globalError.hidden = false;
}

function hideGlobalError() {
  els.globalError.hidden = true;
  els.globalErrorMessage.textContent = "";
}

function errorMessage(reason, fallback) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

function setButtonBusy(button, busy, busyLabel, idleLabel) {
  button.disabled = busy;
  button.textContent = busy ? busyLabel : idleLabel;
  button.setAttribute("aria-busy", String(busy));
}

function formatDate(value) {
  if (!value) return "尚无记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return dateFormatter.format(date);
}

function formatCount(value) {
  const number = Number(value);
  return numberFormatter.format(Number.isFinite(number) ? number : 0);
}

function currentGroup() {
  return state.groups.find((item) => item.group_id === state.selectedGroup) || null;
}

async function apiGet(endpoint, params = {}) {
  return apiResult(await bridge.apiGet(endpoint, params));
}

async function apiPost(endpoint, body = {}) {
  return apiResult(await bridge.apiPost(endpoint, body));
}

async function loadFont() {
  try {
    const config = await apiGet("config");
    if (!config.font_url) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = config.font_url;
    document.head.append(link);
  } catch { /* fallback to system font */ }
}

// Batch load thumbnails for blacklist（分批请求，避免单次请求体量过大）
const BLACKLIST_THUMB_BATCH_SIZE = 16;

async function loadBlacklistThumbnails() {
  const idsWithThumb = state.blacklist
    .filter(item => item.thumb_id)
    .map(item => item.illust_id);

  if (!idsWithThumb.length) {
    state.thumbs = {};
    return;
  }

  state.thumbs = {};
  for (let i = 0; i < idsWithThumb.length; i += BLACKLIST_THUMB_BATCH_SIZE) {
    const chunk = idsWithThumb.slice(i, i + BLACKLIST_THUMB_BATCH_SIZE);
    try {
      const res = await apiPost("image-blacklist/thumb-data-batch", { ids: chunk });
      Object.assign(state.thumbs, res.thumbs || {});
    } catch (error) {
      console.warn("Failed to load blacklist thumbnails:", error);
      break;
    }
  }
}

async function reloadAll() {
  if (state.loading) return;
  state.loading = true;
  const refreshButton = $("refreshBtn");
  setButtonBusy(refreshButton, true, "正在刷新…", "刷新数据");
  hideGlobalError();
  const failures = [];
  let shouldLoadRanking = false;
  try {
    const [overviewResult, groupsResult, safetyResult, blacklistResult] =
      await Promise.allSettled([
        apiGet("overview"),
        apiGet("checkin-groups"),
        apiGet("content-safety"),
        apiGet("image-blacklist"),
      ]);

    if (overviewResult.status === "fulfilled") {
      state.overview = overviewResult.value;
      state.loaded.overview = true;
      renderOverview();
      renderData();
    } else {
      failures.push(errorMessage(overviewResult.reason, "概览数据读取失败"));
      if (!state.loaded.overview) renderOverviewError();
    }

    if (groupsResult.status === "fulfilled") {
      state.groups = Array.isArray(groupsResult.value.groups)
        ? groupsResult.value.groups
        : [];
      state.groups.sort((a, b) => {
        const tA = new Date(a.last_seen_at || 0).getTime();
        const tB = new Date(b.last_seen_at || 0).getTime();
        return tB - tA;
      });
      state.loaded.groups = true;
      if (!state.groups.some((item) => item.group_id === state.selectedGroup)) {
        state.selectedGroup = state.groups[0]?.group_id || "";
      }
      renderGroups();
      shouldLoadRanking = true;
    } else {
      failures.push(errorMessage(groupsResult.reason, "群列表读取失败"));
      if (!state.loaded.groups) renderGroupsError();
    }

    if (safetyResult.status === "fulfilled") {
      state.safety = safetyResult.value;
      state.loaded.safety = true;
      renderSafety();
    } else {
      failures.push(errorMessage(safetyResult.reason, "内容安全数据读取失败"));
      if (!state.loaded.safety) renderSafetyError();
    }

    if (blacklistResult.status === "fulfilled") {
      state.blacklist = Array.isArray(blacklistResult.value.records)
        ? blacklistResult.value.records
        : [];
      state.loaded.blacklist = true;
      await loadBlacklistThumbnails();
      renderBlacklist();
    } else {
      failures.push(errorMessage(blacklistResult.reason, "作品黑名单读取失败"));
      if (!state.loaded.blacklist) renderBlacklistError();
    }

    if (shouldLoadRanking) await loadRanking();

    if (state.loaded.members) {
      try {
        await loadMembers({ reset: true });
      } catch (error) {
        failures.push(errorMessage(error, "成员资料读取失败"));
      }
    }

    if (state.loaded.operations) {
      try {
        await loadOperations();
      } catch (error) {
        failures.push(errorMessage(error, "运营数据读取失败"));
      }
    }

    if (state.loaded.audit) {
      try {
        await loadAudit({ reset: true });
      } catch (error) {
        failures.push(errorMessage(error, "审计日志读取失败"));
      }
    }

    if (failures.length) {
      showGlobalError(failures);
      showToast("部分数据加载失败，可重试", "error");
    }
  } finally {
    state.loading = false;
    setButtonBusy(refreshButton, false, "正在刷新…", "刷新数据");
  }
}

function renderOverview() {
  const data = state.overview;
  const metrics = [
    ["今日群签到", formatCount(data.today_checkins)],
    ["今日活跃群", formatCount(data.active_groups)],
    ["本月签到用户", formatCount(data.month_users)],
    ["作品黑名单", formatCount(data.blacklist_count)],
    ["自定义安全词", formatCount(data.custom_term_count)],
    ["最近备份", data.latest_backup_at ? formatDate(data.latest_backup_at) : "无"],
  ];
  els.overview.innerHTML = metrics.map(([label, value]) => `
    <div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");
}

function renderOverviewError() {
  els.overview.innerHTML = '<div class="metric-error">运行概览暂时无法读取，请使用上方“重新加载”。</div>';
}

function renderGroups() {
  if (!state.groups.length) {
    els.groupList.innerHTML = '<div class="empty">群排行将在群成员首次签到后出现</div>';
    els.selectedGroupName.textContent = "暂无群签到数据";
    els.selectedGroupMeta.textContent = "GROUP SCOPE EMPTY";
    return;
  }
  els.groupList.innerHTML = state.groups.map((group) => `
    <button class="group-button${group.group_id === state.selectedGroup ? " active" : ""}"
      type="button" data-group="${escapeHtml(group.group_id)}">
      <strong>${escapeHtml(group.group_name || group.group_id)}</strong>
      <small>群号: ${escapeHtml(group.group_id)} (${escapeHtml(group.platform)})</small>
      <small>今日 ${formatCount(group.today_count)} · 本月 ${formatCount(group.month_count)}</small>
    </button>
  `).join("");
  els.groupList.querySelectorAll("[data-group]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.selectedGroup = button.dataset.group || "";
      renderGroups();
      await loadRanking();
    });
  });
  const group = currentGroup();
  els.selectedGroupName.textContent = group?.group_name || group?.group_id || "群签到轨道";
  els.selectedGroupMeta.textContent = `${group?.platform || "unknown"} / GROUP ${group?.group_id || "-"}`;
  renderSeasonSettle();
}

function renderGroupsError() {
  els.groupList.innerHTML = '<div class="empty error">群列表读取失败，请重新加载。</div>';
  els.selectedGroupName.textContent = "群签到数据暂不可用";
  els.selectedGroupMeta.textContent = "GROUP SCOPE ERROR";
  els.activityTrack.innerHTML = "";
  els.rankingContent.innerHTML = '<div class="empty error">请在群列表恢复后重试。</div>';
}

async function loadRanking() {
  if (!state.selectedGroup) {
    els.activityTrack.innerHTML = "";
    els.rankingContent.innerHTML = '<div class="empty">等待选择群聊数据</div>';
    return;
  }
  els.rankingContent.innerHTML = '<div class="empty">正在读取榜单，请稍候…</div>';
  try {
    if (state.rankingType === "season") {
      const ranking = await apiGet("checkin-season", {
        group_id: state.selectedGroup,
        limit: 100,
      });
      renderRanking(ranking);
    } else {
      const [ranking, trend] = await Promise.all([
        apiGet("checkin-ranking", {
          group_id: state.selectedGroup,
          type: state.rankingType,
          limit: 100,
        }),
        apiGet("checkin-trend", {
          group_id: state.selectedGroup,
          days: state.trendDays,
        }),
      ]);
      renderRanking(ranking);
      renderTrend(trend.trend || []);
    }
  } catch (error) {
    els.rankingContent.innerHTML = `
      <div class="empty error">
        <p>读取榜单失败：${escapeHtml(error.message)}</p>
        <button type="button" id="retryRankingBtn" class="danger">重新加载</button>
      </div>`;
    const retryBtn = $("retryRankingBtn");
    if (retryBtn) {
      retryBtn.addEventListener("click", loadRanking);
    }
  }
}

function renderRanking(result) {
  const entries = Array.isArray(result.entries) ? result.entries : [];
  if (!entries.length) {
    els.rankingContent.innerHTML = '<div class="empty">当前榜单还没有记录，快去签到吧！</div>';
    return;
  }
  const units = { month: "天", streak: "天", total: "天", season: "天" };
  els.rankingContent.innerHTML = entries.map((entry) => {
    const raw = String(entry.value ?? "");
    let value = "";
    if (state.rankingType === "today") {
      const match = raw.match(/\d{2}:\d{2}:\d{2}/);
      value = match ? match[0] : (raw.slice(11, 19) || raw);
    } else {
      value = `${raw}${units[state.rankingType] || "天"}`;
    }
    return `
      <div class="ranking-row">
        <span class="rank-number">${String(entry.rank).padStart(2, "0")}</span>
        <span class="rank-user"><strong>${escapeHtml(entry.username)}</strong><small>${escapeHtml(entry.user_id)}</small></span>
        <span class="rank-value">${escapeHtml(value)}</span>
      </div>`;
  }).join("");
}

function renderTrend(trend) {
  const max = Math.max(1, ...trend.map((item) => Number(item.count || 0)));
  els.activityTrack.className = `activity-track${state.trendDays === 30 ? " days-30" : ""}`;
  els.trendCaption.textContent = `最近 ${state.trendDays} 天`;
  els.activityTrack.innerHTML = trend.map((item) => {
    const count = Number(item.count || 0);
    const date = String(item.date || "");
    const label = date.slice(5).replace("-", "/");
    const height = count ? Math.max(10, Math.round((count / max) * 92)) : 3;
    return `
      <div class="track-day" tabindex="0" title="${escapeHtml(date)}：${count} 人" aria-label="${escapeHtml(date)}，签到人数 ${count} 人">
        <b>${count}</b><div class="track-bar" style="height:${height}%"></div><small>${escapeHtml(label)}</small>
      </div>`;
  }).join("");
}

function renderMembers() {
  els.memberCount.textContent = `${formatCount(state.members.length)} / ${formatCount(state.memberTotal)} 位`;
  els.memberLoadMore.hidden = state.members.length >= state.memberTotal;
  if (!state.members.length) {
    els.memberList.innerHTML = state.memberQuery
      ? '<div class="empty">没有找到匹配的签到成员</div>'
      : '<div class="empty">目前还没有签到成员资料</div>';
    return;
  }
  els.memberList.innerHTML = state.members.map((member) => `
    <article class="member-row">
      <div class="member-identity">
        <strong>${escapeHtml(member.username || member.user_id)}</strong>
        <small>${escapeHtml(member.user_id)} · 最后签到 ${escapeHtml(member.last_checkin_date || "尚无记录")}</small>
      </div>
      <div class="member-metric"><small>金币</small><strong>${formatCount(member.coins)}</strong></div>
      <div class="member-metric"><small>好感度</small><strong>${Number(member.affection || 0).toFixed(2)}</strong></div>
      <div class="member-metric"><small>累计签到</small><strong>${formatCount(member.total_days)} 天</strong></div>
      <div class="member-metric"><small>连续签到</small><strong>${formatCount(member.streak_days)} 天</strong></div>
      <button class="member-edit" type="button" data-edit-member="${escapeHtml(member.user_id)}"
        aria-label="编辑 ${escapeHtml(member.username || member.user_id)} 的签到数值">编辑</button>
    </article>
  `).join("");
  els.memberList.querySelectorAll("[data-edit-member]").forEach((button) => {
    button.addEventListener("click", () => {
      const member = state.members.find((item) => item.user_id === button.dataset.editMember);
      if (member) openMemberEditor(member, button);
    });
  });
}

function renderMembersError(message = "成员资料读取失败，请重新加载。") {
  els.memberCount.textContent = "读取失败";
  els.memberList.innerHTML = `<div class="empty error">${escapeHtml(message)}</div>`;
  els.memberLoadMore.hidden = true;
}

async function loadMembers({ reset = false } = {}) {
  if (state.membersLoading) return;
  state.membersLoading = true;
  if (reset) {
    state.memberOffset = 0;
    state.members = [];
    els.memberList.innerHTML = '<div class="empty">正在读取成员资料…</div>';
  }
  try {
    const result = await apiGet("checkin-members", {
      query: state.memberQuery,
      limit: state.memberPageSize,
      offset: state.memberOffset,
    });
    const incoming = Array.isArray(result.members) ? result.members : [];
    state.members = reset ? incoming : state.members.concat(incoming);
    state.memberTotal = Number(result.total || 0);
    state.memberOffset = state.members.length;
    state.loaded.members = true;
    renderMembers();
  } catch (error) {
    renderMembersError(error.message);
    throw error;
  } finally {
    state.membersLoading = false;
  }
}

function openMemberEditor(member, trigger) {
  state.selectedMember = member;
  state.memberDialogTrigger = trigger || null;
  els.memberDialogIdentity.textContent = `${member.username || member.user_id} · ${member.user_id}`;
  els.memberUserId.value = member.user_id;
  els.memberCoins.value = String(member.coins ?? 0);
  els.memberAffection.value = Number(member.affection ?? 0).toFixed(2);
  els.memberTotalDays.value = String(member.total_days ?? 0);
  els.memberStreakDays.value = String(member.streak_days ?? 0);
  els.memberFormError.textContent = "";
  els.memberDialog.showModal();
  els.memberCoins.focus();
  els.memberCoins.select();
}

function readMemberForm() {
  const values = {
    user_id: els.memberUserId.value,
    coins: Number(els.memberCoins.value),
    affection: Number(els.memberAffection.value),
    total_days: Number(els.memberTotalDays.value),
    streak_days: Number(els.memberStreakDays.value),
  };
  const integers = [
    ["金币", values.coins],
    ["累计签到", values.total_days],
    ["连续签到", values.streak_days],
  ];
  for (const [label, value] of integers) {
    if (!Number.isInteger(value) || value < 0 || value > 2147483647) {
      throw new Error(`${label}必须是 0 至 2147483647 之间的整数`);
    }
  }
  if (!Number.isFinite(values.affection) || values.affection < -10 || values.affection > 1000000) {
    throw new Error("好感度必须在 -10 至 1000000 之间");
  }
  if (values.streak_days > values.total_days) {
    throw new Error("连续签到不能大于累计签到");
  }
  values.affection = Math.round(values.affection * 100) / 100;
  return values;
}

function renderSafety() {
  const query = els.builtinSearch.value.trim().toLocaleLowerCase("zh-CN");
  const builtin = (state.safety.builtin_terms || []).filter((term) =>
    String(term).toLocaleLowerCase("zh-CN").includes(query));
  const custom = state.safety.custom_terms || [];
  els.builtinCount.textContent = `${state.safety.builtin_terms?.length || 0} 项 / 只读`;
  els.customCount.textContent = `${custom.length} 项`;

  els.builtinTerms.innerHTML = builtin.map((term) => `<span>${escapeHtml(term)}</span>`).join("") || '<div class="empty">没有匹配的内置词</div>';
  els.customTerms.innerHTML = custom.map((item) => `
    <div class="term-item">
      <span><strong>${escapeHtml(item.term)}</strong><small> (${escapeHtml(formatDate(item.added_at))})</small></span>
      <button type="button" data-remove-term="${escapeHtml(item.term)}">删除</button>
    </div>
  `).join("") || '<div class="empty">还没有自定义屏蔽词</div>';

  els.customTerms.querySelectorAll("[data-remove-term]").forEach((button) => {
    button.addEventListener("click", async () => {
      const term = button.dataset.removeTerm;
      if (!await confirmAction("删除自定义屏蔽词", `确认要删除屏蔽词“${term}”吗？`)) return;
      button.disabled = true;
      try {
        await apiPost("content-safety/terms/remove", { term });
        showToast("自定义屏蔽词已成功删除");
        await reloadSafety();
      } catch (error) {
        button.disabled = false;
        showToast(error.message, "error");
      }
    });
  });
}

function renderSafetyError() {
  els.builtinCount.textContent = "读取失败";
  els.customCount.textContent = "读取失败";
  els.builtinTerms.innerHTML = '<div class="empty error">内置安全词暂时无法读取。</div>';
  els.customTerms.innerHTML = '<div class="empty error">自定义安全词暂时无法读取。</div>';
}

async function reloadSafety() {
  try {
    state.safety = await apiGet("content-safety");
    state.loaded.safety = true;
    state.overview.custom_term_count = state.safety.custom_terms?.length || 0;
    renderSafety();
    if (state.loaded.overview) renderOverview();
  } catch (error) {
    renderSafetyError();
    showGlobalError([errorMessage(error, "内容安全数据读取失败")]);
    throw error;
  }
}

function renderBlacklist() {
  els.blacklistCount.textContent = `${state.blacklist.length} 项`;
  els.blacklistContent.innerHTML = state.blacklist.map((item) => {
    const thumbUrl = state.thumbs && state.thumbs[item.illust_id];
    const thumbHtml = thumbUrl
      ? `<div class="blacklist-thumb"><img src="${escapeHtml(thumbUrl)}" alt="作品 ${escapeHtml(item.illust_id)} 缩略图" width="50" height="50" loading="lazy" data-blacklist-thumb /></div>`
      : "";

    return `
      <div class="blacklist-item">
        ${thumbHtml}
        <span class="blacklist-id">#${escapeHtml(item.illust_id)}</span>
        <span class="blacklist-copy">
          <strong>${escapeHtml(item.title || "未获取作品标题")}</strong>
          <small>${escapeHtml(item.author || "作者未知")} · ${escapeHtml(item.reason || "未填写加入原因")} · ${escapeHtml(item.added_by || "管理员")}</small>
        </span>
        <span class="blacklist-meta">${escapeHtml(formatDate(item.added_at))}</span>
        <button type="button" data-remove-illust="${escapeHtml(item.illust_id)}">解除</button>
      </div>
    `;
  }).join("") || '<div class="empty">作品黑名单为空</div>';

  els.blacklistContent.querySelectorAll("[data-blacklist-thumb]").forEach((image) => {
    image.addEventListener("error", () => {
      image.closest(".blacklist-thumb")?.remove();
    }, { once: true });
  });

  els.blacklistContent.querySelectorAll("[data-remove-illust]").forEach((button) => {
    button.addEventListener("click", async () => {
      const illustId = button.dataset.removeIllust;
      if (!await confirmAction("解除作品黑名单", `确定要将作品 ID ${illustId} 移出黑名单吗？`)) return;
      button.disabled = true;
      try {
        await apiPost("image-blacklist/remove", { illust_id: illustId });
        showToast("作品已成功移出黑名单");
        await reloadBlacklist();
      } catch (error) {
        button.disabled = false;
        showToast(error.message, "error");
      }
    });
  });
}

function renderBlacklistError() {
  els.blacklistCount.textContent = "读取失败";
  els.blacklistContent.innerHTML = '<div class="empty error">作品黑名单暂时无法读取。</div>';
}

async function reloadBlacklist() {
  try {
    const result = await apiGet("image-blacklist");
    state.blacklist = result.records || [];
    state.loaded.blacklist = true;
    await loadBlacklistThumbnails();
    state.overview.blacklist_count = state.blacklist.length;
    renderBlacklist();
    if (state.loaded.overview) renderOverview();
  } catch (error) {
    renderBlacklistError();
    showGlobalError([errorMessage(error, "作品黑名单读取失败")]);
    throw error;
  }
}

async function loadOperations() {
  els.statsCards.innerHTML = '<div class="empty">正在读取运营数据…</div>';
  try {
    const [statsResult, subscriptionsResult, remindersResult] = await Promise.allSettled([
      apiGet("checkin-stats"),
      apiGet("subscriptions"),
      apiGet("reminders"),
    ]);
    const failures = [];
    if (statsResult.status === "fulfilled") {
      state.operations = statsResult.value;
      state.loaded.operations = true;
      renderOperations();
    } else {
      failures.push(errorMessage(statsResult.reason, "签到统计读取失败"));
      renderOperationsError();
    }
    if (subscriptionsResult.status === "fulfilled") {
      state.subscriptions = Array.isArray(subscriptionsResult.value.subscriptions)
        ? subscriptionsResult.value.subscriptions
        : [];
      renderSubscriptions();
    } else {
      failures.push(errorMessage(subscriptionsResult.reason, "群订阅读取失败"));
      renderSubscriptionsError();
    }
    if (remindersResult.status === "fulfilled") {
      state.reminders = Array.isArray(remindersResult.value.reminders)
        ? remindersResult.value.reminders
        : [];
      renderReminders();
    } else {
      failures.push(errorMessage(remindersResult.reason, "群提醒读取失败"));
      renderRemindersError();
    }
    if (failures.length) showGlobalError(failures);
  } catch (error) {
    showGlobalError([errorMessage(error, "运营数据读取失败")]);
  }
}

function renderOperations() {
  const data = state.operations;
  const cards = [
    ["7 日活跃用户", formatCount(data.dau_7)],
    ["7 日签到率", `${Number(data.week_checkin_rate ?? 0).toFixed(2)}%`],
    ["30 日签到率", `${Number(data.month_checkin_rate ?? 0).toFixed(2)}%`],
    ["累计用户", formatCount(data.total_users)],
    ["累计签到记录", formatCount(data.total_records)],
    ["累计群数", formatCount(data.total_groups)],
  ];
  els.statsCards.innerHTML = cards.map(([label, value]) => `
    <div class="stat-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");
}

function renderOperationsError() {
  els.statsCards.innerHTML = '<div class="empty error">签到统计暂时无法读取，请使用上方“重新加载”。</div>';
}

function renderSeasonSettle() {
  els.seasonSettleGroup.innerHTML = state.groups.map((group) => `
    <option value="${escapeHtml(group.group_id)}">${escapeHtml(group.group_name || group.group_id)} (${escapeHtml(group.group_id)})</option>
  `).join("");
}

function renderSubscriptions() {
  els.subscriptionCount.textContent = `${state.subscriptions.length} 项`;
  if (!state.subscriptions.length) {
    els.subscriptionList.innerHTML = '<div class="empty">还没有每日一图推送订阅群</div>';
    return;
  }
  els.subscriptionList.innerHTML = state.subscriptions.map((item) => `
    <article class="sub-row${item.enabled ? "" : " disabled"}">
      <div class="sub-identity">
        <strong>${escapeHtml(item.group_name || item.group_id)}</strong>
        <small>群号: ${escapeHtml(item.group_id)} · ${escapeHtml(item.platform || "unknown")}</small>
        <small>推送 ${escapeHtml(item.push_time || "09:00")} · 星期 ${escapeHtml(item.weekdays || "1,2,3,4,5,6,7")} · 标签 ${escapeHtml(item.tag || "无")}</small>
      </div>
      <label class="switch" title="${item.enabled ? "点击停用推送" : "点击启用推送"}">
        <input type="checkbox" data-sub-toggle="${escapeHtml(item.group_id)}" ${item.enabled ? "checked" : ""} aria-label="启用或停用 ${escapeHtml(item.group_name || item.group_id)} 的每日推送" />
        <span></span>
      </label>
      <form class="sub-tag-form" data-sub-tag-form="${escapeHtml(item.group_id)}">
        <input type="text" maxlength="64" autocomplete="off" spellcheck="false" placeholder="推送标签" value="${escapeHtml(item.tag)}" aria-label="订阅标签" />
        <button type="submit" class="secondary">保存标签</button>
      </form>
      <button type="button" class="sub-disable" data-sub-disable="${escapeHtml(item.group_id)}">停用</button>
    </article>
  `).join("");

  els.subscriptionList.querySelectorAll("[data-sub-toggle]").forEach((input) => {
    input.addEventListener("change", async () => {
      const groupId = input.dataset.subToggle;
      input.disabled = true;
      try {
        await apiPost("subscriptions/update", { group_id: groupId, enabled: input.checked });
        showToast(input.checked ? "订阅已启用" : "订阅已停用");
        await loadOperations();
      } catch (error) {
        showToast(error.message || "更新订阅失败", "error");
        await loadOperations();
      }
    });
  });

  els.subscriptionList.querySelectorAll("[data-sub-tag-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const groupId = form.dataset.subTagForm;
      const tag = form.querySelector("input").value.trim();
      const button = form.querySelector("button[type='submit']");
      setButtonBusy(button, true, "正在保存…", "保存标签");
      try {
        await apiPost("subscriptions/update", { group_id: groupId, tag });
        showToast("订阅标签已保存");
        await loadOperations();
      } catch (error) {
        showToast(error.message || "保存标签失败", "error");
        setButtonBusy(button, false, "正在保存…", "保存标签");
      }
    });
  });

  els.subscriptionList.querySelectorAll("[data-sub-disable]").forEach((button) => {
    button.addEventListener("click", async () => {
      const groupId = button.dataset.subDisable;
      if (!await confirmAction("停用订阅", `确认要停用群 ${groupId} 的每日推送吗？`)) return;
      button.disabled = true;
      try {
        await apiPost("subscriptions/update", { group_id: groupId, enabled: false });
        showToast("订阅已停用");
        await loadOperations();
      } catch (error) {
        button.disabled = false;
        showToast(error.message || "停用订阅失败", "error");
      }
    });
  });
}

function renderSubscriptionsError() {
  els.subscriptionCount.textContent = "读取失败";
  els.subscriptionList.innerHTML = '<div class="empty error">群订阅暂时无法读取。</div>';
}

function renderReminders() {
  els.reminderCount.textContent = `${state.reminders.length} 项`;
  if (!state.reminders.length) {
    els.reminderList.innerHTML = '<div class="empty">还没有配置群签到提醒</div>';
    return;
  }
  els.reminderList.innerHTML = state.reminders.map((item) => `
    <article class="sub-row${item.enabled ? "" : " disabled"}">
      <div class="sub-identity">
        <strong>${escapeHtml(item.group_name || item.group_id)}</strong>
        <small>群号: ${escapeHtml(item.group_id)} · ${escapeHtml(item.platform || "unknown")}</small>
        <small>提醒时间 ${escapeHtml(item.remind_time || "21:00")}</small>
      </div>
      <label class="switch" title="${item.enabled ? "点击停用提醒" : "点击启用提醒"}">
        <input type="checkbox" data-reminder-toggle="${escapeHtml(item.group_id)}" ${item.enabled ? "checked" : ""} aria-label="启用或停用 ${escapeHtml(item.group_name || item.group_id)} 的签到提醒" />
        <span></span>
      </label>
      <form class="sub-tag-form" data-reminder-time-form="${escapeHtml(item.group_id)}">
        <input type="time" value="${escapeHtml(item.remind_time || "21:00")}" aria-label="提醒时间" />
        <button type="submit" class="secondary">保存时间</button>
      </form>
    </article>
  `).join("");

  els.reminderList.querySelectorAll("[data-reminder-toggle]").forEach((input) => {
    input.addEventListener("change", async () => {
      const groupId = input.dataset.reminderToggle;
      input.disabled = true;
      try {
        await apiPost("reminders/update", { group_id: groupId, enabled: input.checked });
        showToast(input.checked ? "提醒已启用" : "提醒已停用");
        await loadOperations();
      } catch (error) {
        showToast(error.message || "更新提醒失败", "error");
        await loadOperations();
      }
    });
  });

  els.reminderList.querySelectorAll("[data-reminder-time-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const groupId = form.dataset.reminderTimeForm;
      const remindTime = form.querySelector("input").value;
      const button = form.querySelector("button[type='submit']");
      setButtonBusy(button, true, "正在保存…", "保存时间");
      try {
        await apiPost("reminders/update", { group_id: groupId, remind_time: remindTime });
        showToast("提醒时间已保存");
        await loadOperations();
      } catch (error) {
        showToast(error.message || "保存提醒时间失败", "error");
        setButtonBusy(button, false, "正在保存…", "保存时间");
      }
    });
  });
}

function renderRemindersError() {
  els.reminderCount.textContent = "读取失败";
  els.reminderList.innerHTML = '<div class="empty error">群提醒暂时无法读取。</div>';
}

async function loadAudit({ reset = false } = {}) {
  if (state.auditLoading) return;
  state.auditLoading = true;
  if (reset) {
    state.auditOffset = 0;
    state.auditLogs = [];
    els.auditBody.innerHTML = '<tr><td colspan="6"><div class="empty">正在读取审计日志…</div></td></tr>';
  }
  try {
    const result = await apiGet("audit-logs", {
      action: state.auditAction,
      operator: state.auditOperator,
      limit: state.auditPageSize,
      offset: state.auditOffset,
    });
    const incoming = Array.isArray(result.logs) ? result.logs : [];
    state.auditLogs = reset ? incoming : state.auditLogs.concat(incoming);
    state.auditTotal = Number(result.total || 0);
    state.auditOffset = state.auditLogs.length;
    state.loaded.audit = true;
    renderAudit();
  } catch (error) {
    els.auditBody.innerHTML = `<tr><td colspan="6"><div class="empty error">审计日志读取失败：${escapeHtml(error.message)}</div></td></tr>`;
    els.auditLoadMore.hidden = true;
    throw error;
  } finally {
    state.auditLoading = false;
  }
}

function renderAudit() {
  els.auditEmpty.hidden = state.auditLogs.length > 0;
  els.auditEmpty.textContent = state.auditLogs.length
    ? ""
    : state.auditAction || state.auditOperator
      ? "没有匹配的审计日志，请调整筛选条件。"
      : "还没有审计日志，管理端写操作将在这里留痕。";
  els.auditLoadMore.hidden = state.auditLogs.length >= state.auditTotal;
  els.auditBody.innerHTML = state.auditLogs.map((log) => `
    <tr>
      <td>${escapeHtml(formatDate(log.created_at))}</td>
      <td>${escapeHtml(log.operator || "-")}</td>
      <td><code>${escapeHtml(log.action || "-")}</code></td>
      <td>${escapeHtml(log.target || "-")}</td>
      <td class="audit-detail" title="${escapeHtml(log.detail)}">${escapeHtml(log.detail || "-")}</td>
      <td>${escapeHtml(log.ip || "-")}</td>
    </tr>
  `).join("");
}

function renderData() {
  els.latestBackup.textContent = state.overview.latest_backup_at
    ? `最近备份：${formatDate(state.overview.latest_backup_at)}` : "最近备份：尚无备份记录";
}

function backupFileError(file) {
  if (!file) return "请先选择备份文件。";
  if (!file.name.toLocaleLowerCase("zh-CN").endsWith(".json")) {
    return "只能选择 JSON 备份文件。";
  }
  if (file.size > MAX_BACKUP_BYTES) {
    return "备份文件不能超过 5 MiB。";
  }
  return "";
}

function switchView(name) {
  const validNames = new Set(["ranking", "members", "safety", "data", "operations", "audit"]);
  if (!validNames.has(name)) name = "ranking";
  document.querySelectorAll(".workspace-nav [data-view]").forEach((button) =>
    button.classList.toggle("active", button.dataset.view === name));
  document.querySelectorAll(".workspace").forEach((view) =>
    view.classList.toggle("active", view.id === `${name}View`));
  document.querySelectorAll(".workspace-nav [data-view]").forEach((button) => {
    button.setAttribute("aria-current", button.dataset.view === name ? "page" : "false");
  });
  history.replaceState(null, "", `#${name}`);
  if (name === "members" && !state.loaded.members) {
    loadMembers({ reset: true }).catch((error) => {
      showGlobalError([errorMessage(error, "成员资料读取失败")]);
    });
  }
  if (name === "operations" && !state.loaded.operations) {
    loadOperations().catch((error) => {
      showGlobalError([errorMessage(error, "运营数据读取失败")]);
    });
  }
  if (name === "audit" && !state.loaded.audit) {
    loadAudit({ reset: true }).catch((error) => {
      showGlobalError([errorMessage(error, "审计日志读取失败")]);
    });
  }
}

function confirmAction(title, message) {
  const triggerEl = document.activeElement;
  els.dialogTitle.textContent = title;
  els.dialogMessage.textContent = message;
  els.dialog.showModal();
  return new Promise((resolve) => {
    const onClose = () => {
      els.dialog.removeEventListener("close", onClose);
      if (triggerEl && typeof triggerEl.focus === "function") {
        triggerEl.focus();
      }
      resolve(els.dialog.returnValue === "confirm");
    };
    els.dialog.addEventListener("close", onClose);
  });
}

function bindEvents() {
  document.querySelectorAll(".workspace-nav [data-view]").forEach((button) =>
    button.addEventListener("click", () => switchView(button.dataset.view)));

  document.querySelectorAll("[data-rank]").forEach((button) => button.addEventListener("click", async () => {
    state.rankingType = button.dataset.rank;
    document.querySelectorAll("[data-rank]").forEach((item) => item.classList.toggle("active", item === button));
    await loadRanking();
  }));

  document.querySelectorAll("[data-days]").forEach((button) => button.addEventListener("click", async () => {
    state.trendDays = Number(button.dataset.days || 7);
    document.querySelectorAll("[data-days]").forEach((item) => item.classList.toggle("active", item === button));
    await loadRanking();
  }));

  $("refreshBtn").addEventListener("click", reloadAll);
  $("retryAllBtn").addEventListener("click", reloadAll);
  els.builtinSearch.addEventListener("input", renderSafety);

  $("memberSearchForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    state.memberQuery = els.memberSearch.value.trim();
    try {
      await loadMembers({ reset: true });
    } catch (error) {
      showToast(error.message || "搜索成员失败", "error");
    }
  });

  $("memberClearBtn").addEventListener("click", async () => {
    els.memberSearch.value = "";
    state.memberQuery = "";
    try {
      await loadMembers({ reset: true });
      els.memberSearch.focus();
    } catch (error) {
      showToast(error.message || "成员资料读取失败", "error");
    }
  });

  els.memberLoadMore.addEventListener("click", async () => {
    setButtonBusy(els.memberLoadMore, true, "正在加载…", "加载更多成员");
    try {
      await loadMembers();
    } catch (error) {
      showToast(error.message || "加载更多成员失败", "error");
    } finally {
      setButtonBusy(els.memberLoadMore, false, "正在加载…", "加载更多成员");
    }
  });

  $("memberCancelBtn").addEventListener("click", () => els.memberDialog.close());
  els.memberDialog.addEventListener("close", () => {
    state.selectedMember = null;
    state.memberDialogTrigger?.focus();
    state.memberDialogTrigger = null;
  });
  els.memberForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!els.memberForm.reportValidity()) return;
    const submitButton = $("memberSaveBtn");
    els.memberFormError.textContent = "";
    try {
      const payload = readMemberForm();
      setButtonBusy(submitButton, true, "正在保存…", "保存修改");
      const result = await apiPost("checkin-members/update", payload);
      const index = state.members.findIndex((item) => item.user_id === result.member?.user_id);
      if (index >= 0) state.members[index] = result.member;
      renderMembers();
      els.memberDialog.close();
      showToast("成员签到数值已更新");
    } catch (error) {
      els.memberFormError.textContent = error.message || "保存失败，请检查输入后重试。";
      showToast(els.memberFormError.textContent, "error");
    } finally {
      setButtonBusy(submitButton, false, "正在保存…", "保存修改");
    }
  });

  $("termForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("termInput");
    const submitButton = event.currentTarget.querySelector('button[type="submit"]');
    const term = input.value.trim();
    if (!term) return;
    els.termError.textContent = "";
    setButtonBusy(submitButton, true, "正在添加…", "添加屏蔽词");
    try {
      await apiPost("content-safety/terms/add", { term });
      input.value = "";
      showToast("自定义屏蔽词已成功添加");
      await reloadSafety();
    } catch (error) {
      els.termError.textContent = error.message || "添加失败，请检查内容后重试。";
      input.focus();
      showToast(els.termError.textContent, "error");
    } finally {
      setButtonBusy(submitButton, false, "正在添加…", "添加屏蔽词");
    }
  });

  $("blacklistForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const illustId = $("blacklistId").value.trim();
    const reason = $("blacklistReason").value.trim();
    const submitButton = event.currentTarget.querySelector('button[type="submit"]');
    els.blacklistError.textContent = "";
    setButtonBusy(submitButton, true, "正在加入…", "加入黑名单");
    try {
      await apiPost("image-blacklist/add", { illust_id: illustId, reason });
      event.target.reset();
      showToast("作品已成功加入黑名单");
      await reloadBlacklist();
    } catch (error) {
      els.blacklistError.textContent = error.message || "加入失败，请检查作品 ID 后重试。";
      $("blacklistId").focus();
      showToast(els.blacklistError.textContent, "error");
    } finally {
      setButtonBusy(submitButton, false, "正在加入…", "加入黑名单");
    }
  });

  $("exportBtn").addEventListener("click", async () => {
    const button = $("exportBtn");
    setButtonBusy(button, true, "正在准备…", "下载备份 (.json)");
    try {
      await bridge.download("checkin-export", {}, "checkin-backup.json");
      showToast("签到备份已成功开始下载");
    } catch (error) {
      showToast(error.message || "下载备份失败，请重试。", "error");
    } finally {
      setButtonBusy(button, false, "正在准备…", "下载备份 (.json)");
    }
  });

  els.importFile.addEventListener("change", () => {
    const file = els.importFile.files?.[0];
    const validationError = backupFileError(file);
    els.importBtn.disabled = Boolean(validationError);
    els.importResult.textContent = file && !validationError
      ? `${file.name} · ${(file.size / 1024).toFixed(1)} KiB`
      : validationError;
  });

  els.importBtn.addEventListener("click", async () => {
    const file = els.importFile.files?.[0];
    const validationError = backupFileError(file);
    if (validationError) {
      els.importResult.textContent = validationError;
      els.importFile.focus();
      return;
    }
    if (!await confirmAction("恢复签到数据", "这将会覆盖当前所有签到记录与用户购买主题数据！恢复前系统会自动将现有数据创建为回滚备份。确认要继续吗？")) return;

    els.importBtn.disabled = true;
    els.importResult.textContent = "正在上传并恢复，请稍候…";

    try {
      const result = apiResult(await bridge.upload("checkin-import", file));
      els.importResult.textContent = `恢复成功！已导入：
        ${result.users || 0} 位用户，
        ${result.records || 0} 条签到历史，
        ${result.group_presence || 0} 条群活跃记录，
        ${result.user_themes || 0} 个用户主题，
        回滚文件为 ${result.rollback_file || "未知"}。`;
      els.importFile.value = "";
      showToast("签到备份已成功恢复！");
      await reloadAll();
    } catch (error) {
      els.importResult.textContent = `恢复失败：${error.message || "未知错误"}`;
      showToast(error.message || "恢复失败", "error");
    } finally {
      els.importBtn.disabled = !els.importFile.files?.length;
    }
  });

  els.seasonSettleBtn.addEventListener("click", async () => {
    const groupId = els.seasonSettleGroup.value;
    if (!groupId) {
      showToast("请先选择要结算的群", "error");
      return;
    }
    if (!await confirmAction("赛季结算", `确认要为群 ${groupId} 结算当前赛季奖励吗？前 10 名成员将获得金币奖励，同一赛季不会重复发放。`)) return;
    setButtonBusy(els.seasonSettleBtn, true, "正在结算…", "结算当前赛季");
    els.seasonSettleResult.textContent = "";
    try {
      const result = await apiPost("season-settle", { group_id: groupId });
      if (result.already_settled) {
        els.seasonSettleResult.textContent = `当前赛季（${result.season_key}）已经结算过，不会重复发放。`;
        showToast("该赛季已结算过");
      } else {
        els.seasonSettleResult.textContent = `结算完成：${(result.payouts || []).length} 名成员获得奖励。`;
        showToast("赛季奖励已结算");
      }
    } catch (error) {
      els.seasonSettleResult.textContent = error.message || "赛季结算失败。";
      showToast(els.seasonSettleResult.textContent, "error");
    } finally {
      setButtonBusy(els.seasonSettleBtn, false, "正在结算…", "结算当前赛季");
    }
  });

  els.reportBtn.addEventListener("click", async () => {
    const days = els.reportDays.value || "7";
    const button = els.reportBtn;
    els.reportResult.textContent = "";
    setButtonBusy(button, true, "正在生成…", "下载周报 CSV");
    try {
      await bridge.download("checkin-report", { days }, `checkin-report-${days}d.csv`);
      els.reportResult.textContent = "报表已生成并开始下载";
      showToast("统计报表下载成功");
    } catch (error) {
      els.reportResult.textContent = error.message || "生成报表失败，请重试。";
      showToast(els.reportResult.textContent, "error");
    } finally {
      setButtonBusy(button, false, "正在生成…", "下载周报 CSV");
    }
  });

  $("auditFilterForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    state.auditAction = $("auditAction").value.trim();
    state.auditOperator = $("auditOperator").value.trim();
    try {
      await loadAudit({ reset: true });
    } catch (error) {
      showToast(error.message || "筛选审计日志失败", "error");
    }
  });

  $("auditClearBtn").addEventListener("click", async () => {
    $("auditAction").value = "";
    $("auditOperator").value = "";
    state.auditAction = "";
    state.auditOperator = "";
    try {
      await loadAudit({ reset: true });
      $("auditAction").focus();
    } catch (error) {
      showToast(error.message || "审计日志读取失败", "error");
    }
  });

  els.auditLoadMore.addEventListener("click", async () => {
    setButtonBusy(els.auditLoadMore, true, "正在加载…", "加载更多日志");
    try {
      await loadAudit();
    } catch (error) {
      showToast(error.message || "加载更多日志失败", "error");
    } finally {
      setButtonBusy(els.auditLoadMore, false, "正在加载…", "加载更多日志");
    }
  });
}

async function start() {
  if (!bridge) {
    showToast("AstrBot 页面桥接不可用，请在 AstrBot 内置环境中打开", "error");
    return;
  }
  await bridge.ready();
  bindEvents();
  const initialView = location.hash.slice(1);
  switchView(initialView || "ranking");
  loadFont();
  await reloadAll();
}

start();
