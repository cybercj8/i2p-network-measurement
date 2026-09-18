(function () {
    var STORAGE_KEY = "i2p-theme";
    var CUSTOM_BG_KEY = "i2p-custom-bg";
    var CUSTOM_FG_KEY = "i2p-custom-fg";
    var DEFAULT_CUSTOM_BG = "#ffffff";
    var DEFAULT_CUSTOM_FG = "#1a1a1a";

    var MODES = ["light", "dark", "custom"];
    var LABELS = { light: "🌙 Dark", dark: "🎨 Custom", custom: "☀️ Light" };

    function getMode() {
        var saved = localStorage.getItem(STORAGE_KEY);
        return MODES.indexOf(saved) !== -1 ? saved : null;
    }

    function systemPrefersDark() {
        return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    }

    function effectiveMode() {
        var mode = getMode();
        if (mode) return mode;
        return systemPrefersDark() ? "dark" : "light";
    }

    function applyCustomColors() {
        var bg = localStorage.getItem(CUSTOM_BG_KEY) || DEFAULT_CUSTOM_BG;
        var fg = localStorage.getItem(CUSTOM_FG_KEY) || DEFAULT_CUSTOM_FG;
        document.documentElement.style.setProperty("--bg", bg);
        document.documentElement.style.setProperty("--fg", fg);
    }

    function clearCustomColors() {
        document.documentElement.style.removeProperty("--bg");
        document.documentElement.style.removeProperty("--fg");
    }

    function applyStoredTheme() {
        var mode = getMode();
        if (mode) {
            document.documentElement.setAttribute("data-theme", mode);
        }
        if (mode === "custom") {
            applyCustomColors();
        }
    }

    function setLabel(btn) {
        btn.textContent = LABELS[effectiveMode()];
    }

    function buildCustomPicker() {
        var wrap = document.createElement("span");
        wrap.id = "theme-custom-picker";
        wrap.style.display = "none";

        var bgLabel = document.createElement("label");
        bgLabel.textContent = " bg ";
        bgLabel.style.fontSize = "12px";
        var bgInput = document.createElement("input");
        bgInput.type = "color";
        bgInput.id = "theme-custom-bg";
        bgInput.value = localStorage.getItem(CUSTOM_BG_KEY) || DEFAULT_CUSTOM_BG;

        var fgLabel = document.createElement("label");
        fgLabel.textContent = " text ";
        fgLabel.style.fontSize = "12px";
        var fgInput = document.createElement("input");
        fgInput.type = "color";
        fgInput.id = "theme-custom-fg";
        fgInput.value = localStorage.getItem(CUSTOM_FG_KEY) || DEFAULT_CUSTOM_FG;

        bgInput.addEventListener("input", function () {
            localStorage.setItem(CUSTOM_BG_KEY, bgInput.value);
            document.documentElement.style.setProperty("--bg", bgInput.value);
        });
        fgInput.addEventListener("input", function () {
            localStorage.setItem(CUSTOM_FG_KEY, fgInput.value);
            document.documentElement.style.setProperty("--fg", fgInput.value);
        });

        wrap.appendChild(bgLabel);
        wrap.appendChild(bgInput);
        wrap.appendChild(fgLabel);
        wrap.appendChild(fgInput);
        return wrap;
    }

    function updatePickerVisibility(picker) {
        picker.style.display = effectiveMode() === "custom" ? "flex" : "none";
    }

    function addToggleButton() {
        var btn = document.createElement("button");
        btn.id = "theme-toggle";
        btn.type = "button";
        setLabel(btn);

        var picker = buildCustomPicker();
        updatePickerVisibility(picker);

        btn.addEventListener("click", function () {
            var current = effectiveMode();
            var next = MODES[(MODES.indexOf(current) + 1) % MODES.length];

            document.documentElement.setAttribute("data-theme", next);
            localStorage.setItem(STORAGE_KEY, next);

            if (next === "custom") {
                applyCustomColors();
            } else {
                clearCustomColors();
            }

            setLabel(btn);
            updatePickerVisibility(picker);
        });

        var container = document.createElement("div");
        container.id = "theme-controls";
        container.appendChild(picker);
        container.appendChild(btn);
        document.body.appendChild(container);
    }

    // Applied immediately (before DOMContentLoaded) to avoid a flash of the
    // wrong theme; the toggle button itself waits for <body> to exist.
    applyStoredTheme();

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", addToggleButton);
    } else {
        addToggleButton();
    }
})();
