(() => {
    "use strict";

    const terminal = document.getElementById("terminal");
    if (!terminal) return;

    const byId = (id) => document.getElementById(id);
    const elements = {
        badge: byId("state-badge"),
        amountDisplay: byId("amount-display"),
        amountValue: byId("amount-value"),
        generateBtn: byId("generate-btn"),
        formError: byId("form-error"),
        invoicePanel: byId("invoice-panel"),
        invoiceAmount: byId("invoice-amount"),
        qrImage: byId("qr-image"),
        waitingLabel: byId("waiting-label"),
        expiryText: byId("expiry-text"),
        connectionNotice: byId("connection-notice"),
        invoiceCode: byId("invoice-code"),
        copyBtn: byId("copy-btn"),
        paidAmount: byId("paid-amount"),
        errorMessage: byId("error-message"),
    };

    const screens = {
        empty: byId("screen-empty"),
        generating: byId("screen-generating"),
        waiting: byId("screen-waiting"),
        paid: byId("screen-paid"),
        expired: byId("screen-expired"),
        cancelled: byId("screen-cancelled"),
        error: byId("screen-error"),
    };

    const config = {
        min: Number.parseInt(terminal.dataset.minSats || "1", 10) || 1,
        max: Number.parseInt(terminal.dataset.maxSats || "1000000", 10) || 1000000,
        paymentsReady: terminal.dataset.paymentsReady === "true",
    };

    const numberFormatter = new Intl.NumberFormat("pt-BR");
    const stateLabels = {
        ready: "Pronto",
        generating: "Gerando",
        waiting: "Aguardando",
        paid: "Pago",
        expired: "Expirada",
        cancelled: "Cancelada",
        error: "Erro",
    };

    const keypadButtons = Array.from(document.querySelectorAll(".key"));
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

    const state = {
        mode: "ready",
        digits: "",
        invoice: null,
        requestId: null,
        pollTimer: null,
        countdownTimer: null,
        pollGeneration: 0,
        pollInFlight: false,
        createController: null,
        copyTimer: null,
    };

    function amountValue() {
        if (!state.digits) return 0;
        const value = Number.parseInt(state.digits, 10);
        return Number.isSafeInteger(value) ? value : 0;
    }

    function formatSats(value) {
        return numberFormatter.format(Number(value) || 0);
    }

    function validationError() {
        if (!state.digits) {
            return "Digite um valor em satoshis.";
        }
        const value = amountValue();
        if (!Number.isSafeInteger(value) || value <= 0) {
            return "Digite um valor inteiro maior que zero.";
        }
        if (value < config.min) {
            return `O valor mínimo é ${formatSats(config.min)} sats.`;
        }
        if (value > config.max) {
            return `O valor máximo é ${formatSats(config.max)} sats.`;
        }
        return "";
    }

    function showFormError(message) {
        elements.formError.textContent = message || "";
        elements.formError.hidden = !message;
    }

    function setConnectionNotice(message) {
        elements.connectionNotice.textContent = message || "";
        elements.connectionNotice.hidden = !message;
    }

    function showScreen(name) {
        Object.entries(screens).forEach(([screenName, element]) => {
            if (element) element.hidden = screenName !== name;
        });
    }

    function setMode(mode) {
        state.mode = mode;
        elements.badge.textContent = stateLabels[mode] || mode;
        elements.badge.dataset.state = mode;
        elements.invoicePanel.setAttribute(
            "aria-busy",
            mode === "generating" ? "true" : "false"
        );

        const editable = mode === "ready";
        keypadButtons.forEach((button) => {
            button.disabled = !editable;
        });

        const error = validationError();
        elements.generateBtn.disabled =
            !editable || Boolean(error) || !config.paymentsReady;
        elements.amountDisplay.setAttribute(
            "aria-invalid",
            editable && Boolean(error) && Boolean(state.digits) ? "true" : "false"
        );
    }

    function updateAmountFeedback() {
        if (!state.digits) {
            showFormError("");
            return;
        }
        const value = amountValue();
        if (value > config.max) {
            showFormError(`O valor máximo é ${formatSats(config.max)} sats.`);
            return;
        }
        if (value > 0 && value < config.min) {
            showFormError(`O valor mínimo é ${formatSats(config.min)} sats.`);
            return;
        }
        showFormError("");
    }

    function renderAmount() {
        elements.amountValue.textContent = state.digits
            ? formatSats(amountValue())
            : "0";
        setMode(state.mode);
        updateAmountFeedback();
    }

    function resetRequestIdentity() {
        state.requestId = null;
    }

    function appendDigit(digit) {
        if (state.mode !== "ready") return;

        let next = state.digits;
        if (next === "0") next = "";
        next += digit;
        next = next.replace(/^0+(?=\d)/, "");

        if (next.length > 12) return;

        state.digits = next;
        resetRequestIdentity();
        showFormError("");
        renderAmount();
    }

    function backspace() {
        if (state.mode !== "ready") return;
        state.digits = state.digits.slice(0, -1);
        resetRequestIdentity();
        showFormError("");
        renderAmount();
    }

    function clearAmount() {
        if (state.mode !== "ready") return;
        state.digits = "";
        resetRequestIdentity();
        showFormError("");
        renderAmount();
    }

    function newRequestId() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }

        const bytes = new Uint8Array(16);
        if (window.crypto && typeof window.crypto.getRandomValues === "function") {
            window.crypto.getRandomValues(bytes);
        } else {
            for (let index = 0; index < bytes.length; index += 1) {
                bytes[index] = Math.floor(Math.random() * 256);
            }
        }
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
        return [
            hex.slice(0, 8),
            hex.slice(8, 12),
            hex.slice(12, 16),
            hex.slice(16, 20),
            hex.slice(20),
        ].join("-");
    }

    async function readJson(response) {
        try {
            return await response.json();
        } catch (error) {
            return {};
        }
    }

    function renderQr(paymentRequest) {
        if (typeof window.qrcode !== "function") {
            throw new Error("Biblioteca de QR code indisponível.");
        }
        const qr = window.qrcode(0, "M");
        qr.addData(paymentRequest);
        qr.make();
        elements.qrImage.src = qr.createDataURL(6, 8);
        elements.qrImage.hidden = false;
    }

    function renderExpiry() {
        if (!state.invoice || !state.invoice.expires_at) {
            elements.expiryText.textContent = "Aguardando confirmação do pagamento.";
            return;
        }

        const remaining = new Date(state.invoice.expires_at).getTime() - Date.now();
        if (!Number.isFinite(remaining)) {
            elements.expiryText.textContent = "Aguardando confirmação do pagamento.";
            return;
        }
        if (remaining <= 0) {
            elements.expiryText.textContent = "Tempo esgotado. Verificando o pagamento…";
            return;
        }

        const totalSeconds = Math.ceil(remaining / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        if (hours > 0) {
            elements.expiryText.textContent =
                `Expira em ${hours}h ${String(minutes).padStart(2, "0")}min`;
        } else {
            elements.expiryText.textContent =
                `Expira em ${minutes}:${String(seconds).padStart(2, "0")}`;
        }
    }

    function stopCountdown() {
        if (state.countdownTimer !== null) {
            window.clearInterval(state.countdownTimer);
            state.countdownTimer = null;
        }
    }

    function startCountdown() {
        stopCountdown();
        renderExpiry();
        state.countdownTimer = window.setInterval(renderExpiry, 1000);
    }

    function stopPolling() {
        state.pollGeneration += 1;
        if (state.pollTimer !== null) {
            window.clearTimeout(state.pollTimer);
            state.pollTimer = null;
        }
        state.pollInFlight = false;
    }

    function startPolling() {
        stopPolling();
        const generation = state.pollGeneration;

        const tick = async () => {
            if (generation !== state.pollGeneration || state.mode !== "waiting") {
                return;
            }
            await checkStatus(generation);
            if (generation !== state.pollGeneration || state.mode !== "waiting") {
                return;
            }
            state.pollTimer = window.setTimeout(tick, 3000);
        };

        state.pollTimer = window.setTimeout(tick, 900);
    }

    function updateInvoiceFromServer(data) {
        if (!state.invoice) return;
        state.invoice = { ...state.invoice, ...data };
    }

    function showPaid() {
        stopPolling();
        stopCountdown();
        const amount = state.invoice ? state.invoice.amount_sats : 0;
        elements.paidAmount.textContent = `${formatSats(amount)} sats`;
        elements.waitingLabel.textContent = "Pagamento confirmado";
        setMode("paid");
        showScreen("paid");
        document.title = "PAGO · browser-POS";
    }

    function showExpired() {
        stopPolling();
        stopCountdown();
        setMode("expired");
        showScreen("expired");
        document.title = "Expirada · browser-POS";
    }

    function handleStatus(data) {
        if (!state.invoice) return;
        updateInvoiceFromServer(data);

        if (data.paid === true || data.status === "paid") {
            showPaid();
            return;
        }

        if (data.status === "expired") {
            showExpired();
            return;
        }

        elements.waitingLabel.textContent = "Aguardando pagamento";
        setConnectionNotice("");
        renderExpiry();
    }

    async function checkStatus(generation) {
        if (!state.invoice) return;
        if (state.pollInFlight) return;
        if (generation !== state.pollGeneration) return;

        state.pollInFlight = true;
        const invoiceId = state.invoice.invoice_id;

        try {
            const response = await fetch(
                `/api/invoices/${encodeURIComponent(invoiceId)}/status/`,
                {
                    method: "GET",
                    headers: { Accept: "application/json" },
                    cache: "no-store",
                }
            );
            const data = await readJson(response);

            if (generation !== state.pollGeneration) return;

            if (!response.ok) {
                if (response.status === 404) {
                    stopPolling();
                    stopCountdown();
                    elements.errorMessage.textContent =
                        data.error || "Esta cobrança não está mais disponível nesta sessão.";
                    setMode("error");
                    showScreen("error");
                    return;
                }
                setConnectionNotice(
                    data.error || "Falha de conexão. Tentando novamente…"
                );
                return;
            }

            handleStatus(data);
        } catch (error) {
            if (generation !== state.pollGeneration) return;
            setConnectionNotice("Sem conexão com o servidor. Tentando novamente…");
        } finally {
            if (generation === state.pollGeneration) {
                state.pollInFlight = false;
            }
        }
    }

    function showWaiting(data) {
        elements.invoiceAmount.textContent = `${formatSats(data.amount_sats)} sats`;
        elements.invoiceCode.value = data.payment_request || "";
        elements.waitingLabel.textContent = "Aguardando pagamento";
        setConnectionNotice("");
        renderQr(data.payment_request);
        renderExpiry();
    }

    async function generateInvoice() {
        if (state.mode !== "ready") return;

        const error = validationError();
        if (error) {
            showFormError(error);
            return;
        }
        if (!config.paymentsReady) {
            showFormError("Serviço de pagamentos não configurado no servidor.");
            return;
        }

        showFormError("");
        const requestId = state.requestId || (state.requestId = newRequestId());
        const controller = new AbortController();
        state.createController = controller;
        const timeout = window.setTimeout(() => controller.abort(), 20000);

        setMode("generating");
        showScreen("generating");

        try {
            const response = await fetch("/api/invoices/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Accept: "application/json",
                    "X-CSRFToken": csrfToken,
                },
                body: JSON.stringify({
                    amount_sats: amountValue(),
                    request_id: requestId,
                }),
                signal: controller.signal,
                cache: "no-store",
            });
            const data = await readJson(response);

            if (state.createController !== controller) return;

            if (!response.ok) {
                const fallback =
                    response.status === 403
                        ? "Sessão expirada. Recarregue a página e tente novamente."
                        : "Não foi possível gerar a fatura.";
                throw new Error(data.error || fallback);
            }

            if (!data.invoice_id || !data.payment_request || !data.amount_sats) {
                throw new Error("Resposta inválida do servidor.");
            }

            state.invoice = data;
            showWaiting(data);
            setMode("waiting");
            showScreen("waiting");
            startCountdown();
            startPolling();
        } catch (error) {
            if (state.createController !== controller) return;
            const message =
                error && error.name === "AbortError"
                    ? "O servidor demorou demais para responder. Verifique a conexão e tente novamente."
                    : error && error.message
                        ? error.message
                        : "Falha ao gerar a fatura.";
            elements.errorMessage.textContent = message;
            setMode("error");
            showScreen("error");
        } finally {
            window.clearTimeout(timeout);
            if (state.createController === controller) {
                state.createController = null;
            }
        }
    }

    function abandonInvoice() {
        if (state.mode !== "waiting") return;
        const confirmed = window.confirm(
            "Abandonar esta cobrança? A fatura continua válida no serviço até expirar."
        );
        if (!confirmed) return;

        stopPolling();
        stopCountdown();
        state.invoice = null;
        setMode("cancelled");
        showScreen("cancelled");
        document.title = "Cancelada · browser-POS";
    }

    function resetTerminal() {
        if (state.createController) {
            state.createController.abort();
            state.createController = null;
        }
        stopPolling();
        stopCountdown();

        state.invoice = null;
        state.requestId = null;
        state.digits = "";
        showFormError("");
        setConnectionNotice("");
        window.clearTimeout(state.copyTimer);
        elements.copyBtn.textContent = "Copiar";
        elements.qrImage.removeAttribute("src");
        elements.invoiceCode.value = "";
        elements.paidAmount.textContent = "0 sats";
        document.title = "browser-POS · Lightning";
        state.mode = "ready";
        renderAmount();
        setMode("ready");
        showScreen("empty");
    }

    async function copyInvoice() {
        const code = elements.invoiceCode.value;
        if (!code) return;

        try {
            await navigator.clipboard.writeText(code);
        } catch (error) {
            elements.invoiceCode.focus();
            elements.invoiceCode.select();
            try {
                document.execCommand("copy");
            } catch (copyError) {
                return;
            }
            window.getSelection()?.removeAllRanges();
        }

        elements.copyBtn.textContent = "Copiado";
        window.clearTimeout(state.copyTimer);
        state.copyTimer = window.setTimeout(() => {
            elements.copyBtn.textContent = "Copiar";
        }, 1600);
    }

    keypadButtons.forEach((button) => {
        button.addEventListener("click", () => {
            const action = button.dataset.action;
            if (action === "clear") {
                clearAmount();
                return;
            }
            if (action === "backspace") {
                backspace();
                return;
            }
            if (button.dataset.digit) {
                appendDigit(button.dataset.digit);
            }
        });
    });

    elements.generateBtn.addEventListener("click", generateInvoice);
    elements.copyBtn.addEventListener("click", copyInvoice);
    byId("abandon-btn").addEventListener("click", abandonInvoice);
    byId("new-charge-btn").addEventListener("click", resetTerminal);
    byId("expired-new-btn").addEventListener("click", resetTerminal);
    byId("cancelled-back-btn").addEventListener("click", resetTerminal);
    byId("error-back-btn").addEventListener("click", resetTerminal);

    byId("error-retry-btn").addEventListener("click", () => {
        if (state.mode !== "error") return;
        setMode("ready");
        showScreen("empty");
        generateInvoice();
    });

    byId("expired-check-btn").addEventListener("click", () => {
        if (!state.invoice) {
            resetTerminal();
            return;
        }
        setMode("waiting");
        showScreen("waiting");
        startCountdown();
        startPolling();
    });

    document.addEventListener("keydown", (event) => {
        const target = event.target;
        const isTextTarget =
            target &&
            (target.tagName === "INPUT" ||
                target.tagName === "TEXTAREA" ||
                target.isContentEditable);
        if (isTextTarget || event.metaKey || event.ctrlKey || event.altKey) return;

        if (state.mode === "ready") {
            if (/^\d$/.test(event.key)) {
                event.preventDefault();
                appendDigit(event.key);
                return;
            }
            if (event.key === "Backspace") {
                event.preventDefault();
                backspace();
                return;
            }
            if (event.key === "Delete" || event.key === "Escape") {
                event.preventDefault();
                clearAmount();
                return;
            }
            if (event.key === "Enter") {
                event.preventDefault();
                generateInvoice();
            }
            return;
        }

        if (state.mode === "waiting" && event.key === "Escape") {
            event.preventDefault();
            abandonInvoice();
        }
    });

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && state.mode === "waiting" && state.invoice) {
            checkStatus(state.pollGeneration);
        }
    });

    window.addEventListener("offline", () => {
        if (state.mode === "waiting") {
            setConnectionNotice("Sem conexão com o servidor. Tentando novamente…");
        }
    });

    window.addEventListener("online", () => {
        if (state.mode === "waiting" && state.invoice) {
            checkStatus(state.pollGeneration);
        }
    });

    renderAmount();
    setMode("ready");
    showScreen("empty");
})();
