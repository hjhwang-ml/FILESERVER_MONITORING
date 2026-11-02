// filehub v4 — 공유 드라이브 화면 동작 (2026-10-21)
// 여러 파일 업로드 진행 창 · 격자/목록 보기 · 새 폴더 창 · 끌어 놓기 · 토스트 · 확인창 · 라이트/다크
// JS 가 꺼져 있어도 목록 · 받기 · 삭제는 일반 링크/폼으로 된다.
(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function store(key, val) {           // 브라우저 저장소는 막혀 있을 수 있다 → 실패해도 화면은 그대로
    try {
      if (val === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, val);
    } catch (_) { return null; }
  }
  function fmt(bytes) {
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (bytes >= 1024 && i < u.length - 1) { bytes /= 1024; i++; }
    return (i ? bytes.toFixed(1) : bytes) + " " + u[i];
  }

  // ---- 라이트 / 다크 모드 (이 브라우저에만 기억 · 처음엔 OS 설정을 따른다) ----
  function currentTheme() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  $all("[data-theme-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      store("filehub.theme", next);
    });
  });

  // ---- 확인창: <form data-confirm="메시지"> ----
  $all("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // ---- 「내용 바꾸기」: 파일을 고르면 바로 보낸다 ----
  $all("input[data-autosubmit]").forEach(function (input) {
    input.addEventListener("change", function () { if (input.files.length) input.form.submit(); });
  });

  // ---- 왼쪽 메뉴 (좁은 화면) ----
  $all("[data-nav-toggle]").forEach(function (el) {
    el.addEventListener("click", function () { document.body.classList.toggle("nav-open"); });
  });

  // ---- 열린 메뉴(새로 만들기 · ⋮)는 바깥을 누르면 닫는다 ----
  document.addEventListener("click", function (e) {
    $all("details.new-menu[open], details.kebab[open]").forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });

  // ---- 토스트: 4초 뒤 사라짐 ----
  $all(".toast").forEach(function (t, i) {
    setTimeout(function () { t.classList.add("is-gone"); }, 4000 + i * 400);
    setTimeout(function () { t.remove(); }, 4600 + i * 400);
  });
  function toast(msg, kind) {
    var box = $(".toasts");
    if (!box) return;
    var t = document.createElement("div");
    t.className = "toast" + (kind === "error" ? " toast-error" : "");
    t.textContent = msg;
    box.appendChild(t);
    setTimeout(function () { t.classList.add("is-gone"); }, 4000);
    setTimeout(function () { t.remove(); }, 4600);
  }

  // ---- 격자 / 목록 보기 (이 브라우저에만 기억) ----
  var items = $("#items");
  var viewBtns = $all("[data-view]");
  function setView(v) {
    if (!items) return;
    items.classList.toggle("view-grid", v !== "list");
    items.classList.toggle("view-list", v === "list");
    viewBtns.forEach(function (b) { b.classList.toggle("on", b.dataset.view === v); });
  }
  setView(store("filehub.view") === "list" ? "list" : "grid");
  viewBtns.forEach(function (b) {
    b.addEventListener("click", function () { setView(b.dataset.view); store("filehub.view", b.dataset.view); });
  });

  // ---- 새 폴더 창 ----
  var dlg = $("#folder-dlg");
  $all("[data-open-folder]").forEach(function (b) {
    b.addEventListener("click", function () {
      $all("details.new-menu[open]").forEach(function (d) { d.removeAttribute("open"); });
      if (!dlg) return;
      if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
      var name = $("input[name=name]", dlg);
      name.value = "";
      name.focus();
    });
  });
  $all("[data-close-dialog]").forEach(function (b) {
    b.addEventListener("click", function () { if (dlg.close) dlg.close(); else dlg.removeAttribute("open"); });
  });

  // ---- 여러 파일 업로드 ----
  var ctx = $("#upload-ctx");
  var pick = $("#pick");
  var panel = $("#upq");
  var list = $("#upq-list");
  var title = $("#upq-title");
  var DONE_KEY = "filehub.lastUpload";
  if (!ctx || !pick || !panel) return;

  $("#upq-close").addEventListener("click", function () { panel.hidden = true; list.innerHTML = ""; });

  function row(name) {
    var li = document.createElement("li");
    li.innerHTML = '<span class="name"></span><span class="st">대기</span><div class="bar"><span></span></div>';
    $(".name", li).textContent = name;
    list.appendChild(li);
    return li;
  }
  function setRow(li, state, pct, msg) {
    var st = $(".st", li);
    st.className = "st" + (state === "ok" ? " st-ok" : state === "bad" ? " st-bad" : "");
    st.textContent = state === "ok" ? "완료" : state === "bad" ? "실패" : pct + "%";
    $(".bar span", li).style.width = (state === "bad" ? 100 : pct) + "%";
    if (state === "bad") $(".bar span", li).style.background = "var(--danger)";
    if (msg) {
      var m = document.createElement("span");
      m.className = "msg";
      m.textContent = msg;
      li.appendChild(m);
    }
  }
  function summary(ok, bad) {
    return bad ? "업로드 " + ok + "개 완료 · " + bad + "개 실패" : "업로드 완료 " + ok + "개";
  }

  // 새로고침 뒤에도 방금 결과를 보여 준다 (「업로드 완료 3개」 창)
  (function restore() {
    var raw = null;
    try { raw = window.sessionStorage.getItem(DONE_KEY); window.sessionStorage.removeItem(DONE_KEY); } catch (_) {}
    if (!raw) return;
    try {
      var data = JSON.parse(raw);
      data.rows.forEach(function (r) { setRow(row(r.name), r.ok ? "ok" : "bad", 100, r.msg); });
      title.textContent = summary(data.ok, data.bad);
      panel.hidden = false;
    } catch (_) {}
  })();

  function uploadOne(file, li) {
    return new Promise(function (resolve) {
      var fd = new FormData();
      fd.append("csrf", ctx.dataset.csrf);
      fd.append("folder_id", ctx.dataset.folder || "");
      fd.append("file", file);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", ctx.dataset.url);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.upload.addEventListener("progress", function (ev) {
        if (ev.lengthComputable) setRow(li, "run", Math.min(99, Math.round(ev.loaded / ev.total * 100)));
      });
      xhr.addEventListener("load", function () {
        var msg = "";
        try { msg = JSON.parse(xhr.responseText).message || ""; } catch (_) {
          // JSON 이 아니면 Nginx 가 앞에서 막은 것 (앱까지 오지 않음)
          if (xhr.status === 413) msg = "파일이 너무 큽니다 (Nginx client_max_body_size 초과).";
          else if (xhr.status === 502 || xhr.status === 504) msg = "웹 앱이 응답하지 않습니다 (" + xhr.status + ").";
          else if (xhr.responseURL && xhr.responseURL.indexOf(ctx.dataset.url) < 0) msg = "로그인이 풀렸습니다. 새로고침 후 다시 로그인하세요.";
        }
        var ok = xhr.status >= 200 && xhr.status < 300 && (!xhr.responseURL || xhr.responseURL.indexOf(ctx.dataset.url) >= 0);
        if (!ok && !msg) msg = "업로드하지 못했습니다 (HTTP " + xhr.status + ").";
        setRow(li, ok ? "ok" : "bad", 100, ok ? "" : (msg || "업로드하지 못했습니다."));
        resolve({ name: file.name, ok: ok, msg: ok ? "" : msg });
      });
      xhr.addEventListener("error", function () {
        setRow(li, "bad", 100, "네트워크 오류");
        resolve({ name: file.name, ok: false, msg: "네트워크 오류" });
      });
      xhr.send(fd);
    });
  }

  var busy = false;
  function start(files) {
    if (busy || !files.length) return;
    busy = true;
    list.innerHTML = "";
    panel.hidden = false;
    title.textContent = "업로드 중 · " + files.length + "개";
    var rows = files.map(function (f) { return row(f.name + "  ·  " + fmt(f.size)); });
    var results = [];
    // 하나씩 차례로 (동시에 보내면 gunicorn 스레드를 한꺼번에 잡는다)
    files.reduce(function (p, f, i) {
      return p.then(function () { return uploadOne(f, rows[i]).then(function (r) { results.push(r); }); });
    }, Promise.resolve()).then(function () {
      busy = false;
      var ok = results.filter(function (r) { return r.ok; }).length;
      var bad = results.length - ok;
      title.textContent = summary(ok, bad);
      if (ok) {
        try {
          window.sessionStorage.setItem(DONE_KEY, JSON.stringify({ ok: ok, bad: bad, rows: results }));
        } catch (_) {}
        setTimeout(function () { window.location.href = ctx.dataset.back; }, 700);
      } else {
        toast("업로드하지 못했습니다.", "error");
      }
    });
  }

  $all("[data-pick-files]").forEach(function (b) {
    b.addEventListener("click", function () {
      $all("details.new-menu[open]").forEach(function (d) { d.removeAttribute("open"); });
      pick.value = "";
      pick.click();
    });
  });
  pick.addEventListener("change", function () { start(Array.prototype.slice.call(pick.files)); });

  // ---- 끌어 놓기: 파일 화면 위 아무 데나 ----
  var drive = $("#drive");
  if (drive && $("#drop-hint")) {
    var depth = 0;
    drive.addEventListener("dragenter", function (e) {
      if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types, "Files") < 0) return;
      depth++; drive.classList.add("is-over");
    });
    drive.addEventListener("dragover", function (e) { e.preventDefault(); });
    drive.addEventListener("dragleave", function () { depth = Math.max(0, depth - 1); if (!depth) drive.classList.remove("is-over"); });
    drive.addEventListener("drop", function (e) {
      e.preventDefault(); depth = 0; drive.classList.remove("is-over");
      if (e.dataTransfer && e.dataTransfer.files.length) start(Array.prototype.slice.call(e.dataTransfer.files));
    });
  }
})();