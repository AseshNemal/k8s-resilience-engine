/* Workflow workspace: all results and progression come from the component API. */
(function () {
    'use strict';
    const $ = role => document.querySelector(`[data-role="${role}"]`);
    const guidedForm = $('guided-analysis-form');
    const guidedStatus = $('guided-analysis-status');
    const guidedDiscovery = $('guided-discovery');
    const guidedOperation = $('guided-operation');
    const guidedInspectButton = $('guided-inspect-button');
    const guidedResult = $('guided-result');
    const guidedService = guidedForm?.elements.namedItem('entryService');
    const guidedServiceField = $('guided-service-field');
    const offlineForm = $('offline-analysis-form');
    const offlineStatus = $('offline-analysis-status');
    const offlineResult = $('offline-analysis-result');
    const preferencesKey = 'workflow-source-preferences-v1';
    let guidedWorkflows = [];
    let guidedConnectionVersion = 0;
    let renderVersion = 0;
    let activeCampaign = null;
    let offlineVersion = 0;
    let fieldCounter = 0;
    let discoveryPoller = null;
    function guidedText(tag, className, value) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = value;
        return element;
    }
    function field(label, type = 'text', value = '', help = '') {
        const wrapper = guidedText('div', 'workspace-field', '');
        const caption = guidedText('label', '', label);
        const input = document.createElement(type === 'textarea' ? 'textarea' : 'input');
        if (type !== 'textarea') input.type = type; else input.rows = 4;
        input.value = value;
        input.id = `workflow-field-${++fieldCounter}`;
        caption.htmlFor = input.id;
        wrapper.append(caption, input);
        if (help) {
            const hint = guidedText('small', 'workspace-help', help);
            hint.id = `${input.id}-help`;
            input.setAttribute('aria-describedby', hint.id);
            if (type === 'checkbox') {
                const copy = guidedText('div', '', '');
                copy.append(caption, hint);
                wrapper.append(copy);
            } else wrapper.append(hint);
        }
        return {label: wrapper, input};
    }
    function describeOperation(operation) {
        const match = /^(GET|POST|PUT|PATCH|DELETE)\s+(\/\S*)$/i.exec(operation);
        if (!match) return {direct: false, text: `${operation} is an internal or custom operation. To run it, provide an HTTP execution definition, or choose an HTTP request from the service that receives user traffic.`};
        const method = match[1].toUpperCase();
        return {direct: true, text: `The runner will send ${method} ${match[2]} to your application. ${method === 'GET' ? 'Supply request parameters if this page needs them.' : 'Supply the request data and any required session details before running. Repeating this request may change application data.'} Selecting a trace does not copy its request body, cookies or earlier steps.`};
    }
    function button(label, action, secondary = false) {
        const item = guidedText('button', secondary ? 'secondary-button' : 'experiment-button', label);
        item.type = 'button';
        item.addEventListener('click', action);
        return item;
    }
    function resultCard(label, value, detail = '') {
        const item = guidedText('div', 'classification-result', '');
        item.append(guidedText('span', '', label), guidedText('strong', '', value));
        if (detail) item.append(guidedText('small', '', detail));
        return item;
    }
    function details(title, ...children) {
        const item = document.createElement('details');
        item.append(guidedText('summary', '', title), ...children);
        return item;
    }
    function message(target, text, error = false) {
        target.textContent = text;
        target.classList.toggle('is-error', error);
        target.setAttribute('role', 'status');
    }
    function setGuidedStatus(text, error = false) { message(guidedStatus, text, error); }
    function downloadJsonArtifact(filename, value) {
        const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'}));
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    async function requestScenario(endpoint, options = {}) {
        const response = await fetch(endpoint, {...options, headers: {Accept: 'application/json', ...(options.headers || {})}});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            let detail = data.detail;
            if (Array.isArray(detail)) detail = detail.map(item => `${(item.loc || []).filter(x => x !== 'body').join(' → ')}: ${item.msg}`).join('; ');
            else if (detail && typeof detail === 'object') detail = detail.message || JSON.stringify(detail);
            const error = new Error(detail || `Request failed (${response.status}). Try again.`);
            error.status = response.status;
            throw error;
        }
        return data;
    }
    function postCampaignAction(url, payload, actor = 'workflow-ui') {
        return requestScenario(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Actor': actor}, body: JSON.stringify(payload)});
    }
    const campaignUrl = campaign => `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}`;
    function stored(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } }
    function remember(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Storage is optional. */ } }
    function setSelection(id, inspectionId = null) {
        const url = new URL(location.href);
        if (id) url.searchParams.set('workflow', id); else url.searchParams.delete('workflow');
        if (inspectionId) url.searchParams.set('inspection', inspectionId); else url.searchParams.delete('inspection');
        history.replaceState(null, '', url);
        document.querySelectorAll('[data-campaign]').forEach(item => item.setAttribute('aria-current', String(item.dataset.campaign === id)));
    }
    function sourceBadge(text) {
        const badge = document.querySelector('.topbar .pill-badge--teal');
        if (badge) badge.textContent = text;
    }
    function openSource() {
        renderVersion += 1;
        guidedConnectionVersion += 1;
        offlineVersion += 1;
        activeCampaign = null;
        setSelection(null);
        resetGuidedDiscovery();
        setGuidedStatus('Connect to find your recorded services and operations.');
        $('source-panel').hidden = false;
        guidedResult.hidden = true;
        offlineResult.hidden = true;
        sourceBadge('No workflow selected');
        $('source-title').focus();
    }
    async function loadSavedWorkflows(resume = false) {
        const initialVersion = renderVersion;
        const status = $('saved-status');
        const list = $('saved-workflows');
        message(status, 'Loading saved workflows…');
        try {
            const campaigns = await requestScenario('/api/workflows/experiment-campaigns');
            list.replaceChildren();
            message(status, campaigns.length ? `${campaigns.length} saved workflow${campaigns.length === 1 ? '' : 's'}` : 'No saved workflows yet. Connect tracing to create one.');
            campaigns.forEach(campaign => {
                const item = button('', () => renderCampaign(campaign), true);
                item.dataset.campaign = campaign.campaignId;
                item.append(guidedText('strong', '', campaign.workflowName), guidedText('span', '', campaign.environmentId));
                if (campaign.createdAt) item.append(guidedText('small', '', new Date(campaign.createdAt).toLocaleString()));
                item.setAttribute('aria-current', String(campaign.campaignId === activeCampaign?.campaignId));
                list.append(item);
            });
            if (resume && initialVersion === renderVersion) {
                const params = new URL(location.href).searchParams;
                const id = params.get('workflow');
                const inspectionId = params.get('inspection');
                if (!id && inspectionId) {
                    const session = await requestScenario(`/api/workflows/analysis-sessions/${encodeURIComponent(inspectionId)}`);
                    if (initialVersion === renderVersion) renderGuidedSession(session);
                }
                if (id) {
                    const selected = campaigns.find(item => item.campaignId === id);
                    if (selected) await renderCampaign(selected);
                    else message(status, 'This saved workflow is unavailable. Choose another or create a new workflow.', true);
                }
            }
        } catch (error) { message(status, `Could not load saved workflows: ${error.message}`, true); }
    }
    function selectedGuidedWorkflow(workflows, selection) {
        return /^\d+$/.test(selection) ? workflows[Number(selection)] : undefined;
    }
    function guidedConnectionPayload() {
        const input = name => guidedForm.elements.namedItem(name).value.trim();
        const authentication = {type: input('traceAuthentication')};
        if (authentication.type === 'bearer_env') authentication.secretEnv = input('traceSecretEnv');
        return {environmentId: input('environmentId'), evidenceTier: input('evidenceTier'), traceSource: {type: input('traceSourceType'), endpoint: input('traceEndpoint'), authentication}, entryService: input('entryService'), lookback: input('lookback')};
    }
    function resetGuidedDiscovery() {
        discoveryPoller?.stop();
        guidedConnectionVersion += 1;
        guidedWorkflows = [];
        guidedService.replaceChildren(new Option('Connect to load services', ''));
        guidedService.disabled = true;
        guidedServiceField.hidden = true;
        guidedDiscovery.hidden = true;
        guidedResult.hidden = true;
        guidedInspectButton.disabled = true;
        guidedForm.querySelector('button[type="submit"]').disabled = false;
    }
    function syncGuidedAuthentication() {
        const needsToken = guidedForm.elements.traceAuthentication.value === 'bearer_env';
        const token = guidedForm.elements.traceSecretEnv;
        token.disabled = !needsToken;
        token.required = needsToken;
        $('guided-secret-field').hidden = !needsToken;
    }
    async function connectGuidedSource(event) {
        event.preventDefault();
        if (!guidedForm.reportValidity()) return;
        resetGuidedDiscovery();
        const version = guidedConnectionVersion;
        const submit = guidedForm.querySelector('button[type="submit"]');
        submit.disabled = true;
        setGuidedStatus('Connecting…');
        try {
            const payload = guidedConnectionPayload();
            const services = await postCampaignAction('/api/workflows/analysis-sessions/services', payload);
            if (version !== guidedConnectionVersion) return;
            // Store supplied settings only, never token values or environment variable names.
            remember(preferencesKey, {environmentId: payload.environmentId, evidenceTier: payload.evidenceTier, sourceType: payload.traceSource.type, endpoint: payload.traceSource.endpoint, lookback: payload.lookback});
            guidedService.replaceChildren(new Option(services.length ? 'Choose an entry service' : 'No services found', ''));
            services.forEach(name => guidedService.append(new Option(name, name)));
            guidedService.disabled = !services.length;
            guidedServiceField.hidden = false;
            setGuidedStatus(services.length ? 'Connected. Choose the service that receives the workflow request.' : 'Connected, but no services were found. Check the URL and time window.');
            if (services.length === 1) { guidedService.value = services[0]; await discoverGuidedWorkflows(); }
        } catch (error) { if (version === guidedConnectionVersion) setGuidedStatus(`Could not connect: ${error.message}`, true); }
        finally { if (version === guidedConnectionVersion) submit.disabled = false; }
    }
    function findWorkflowsAutomatically() {
        if (!guidedForm.reportValidity()) return;
        if (discoveryPoller?.isRunning()) { discoveryPoller.refresh(); return; }
        resetGuidedDiscovery();
        const version = guidedConnectionVersion;
        const {traceSource, lookback} = guidedConnectionPayload();
        const payload = {traceSource, lookback};
        const render = report => {
            if (version !== guidedConnectionVersion) return;
            const previous = selectedGuidedWorkflow(guidedWorkflows, guidedOperation.value);
            guidedWorkflows = report.workflows;
            guidedOperation.replaceChildren(new Option(report.workflows.length ? 'Choose an operation' : report.message, ''));
            report.workflows.forEach((item, index) => {
                guidedOperation.append(new Option(`${item.operationName} · ${item.rootService} · ${item.traceCount} traces`, String(index)));
                if (previous?.rootService === item.rootService && previous?.operationName === item.operationName) guidedOperation.value = String(index);
            });
            guidedDiscovery.hidden = false;
            guidedInspectButton.disabled = !selectedGuidedWorkflow(guidedWorkflows, guidedOperation.value);
            const coverage = report.truncated ? ' The scan reached its limit; other workflows may exist.' : '';
            setGuidedStatus(`${report.message}${coverage}`);
            const help = $('guided-operation-help');
            if (help) help.textContent = 'Entry services and recorded operations were found by the backend. Choose the action you want to test; observed calls are not yet validated dependencies.';
        };
        discoveryPoller = window.WorkflowDiscoveryPoller.create({
            // The discovery API handles availability for every configured trace backend.
            checkHealth: async () => true,
            discover: () => postCampaignAction('/api/workflows/discovery', payload),
            onChecking: () => setGuidedStatus('Finding recorded workflows across services…'),
            onWaiting: () => setGuidedStatus('Waiting for the trace service…'),
            onReport: render,
            onError: error => {
                if (version !== guidedConnectionVersion) return;
                setGuidedStatus(error.message, true);
                if (error.status === 422) discoveryPoller.stop();
            },
            isHidden: () => document.hidden,
        });
        discoveryPoller.start();
    }
    async function discoverGuidedWorkflows() {
        discoveryPoller?.stop();
        if (!guidedService.value) return;
        const version = ++guidedConnectionVersion;
        guidedResult.hidden = true;
        guidedDiscovery.hidden = true;
        setGuidedStatus('Loading recorded operations…');
        try {
            const workflows = await postCampaignAction('/api/workflows/analysis-sessions/discover', guidedConnectionPayload());
            if (version !== guidedConnectionVersion) return;
            guidedWorkflows = workflows;
            guidedOperation.replaceChildren(new Option(workflows.length ? 'Choose an operation' : 'No operations found', ''));
            workflows.forEach((item, index) => guidedOperation.append(new Option(`${item.operationName} · ${item.traceCount} traces`, String(index))));
            guidedDiscovery.hidden = false;
            guidedInspectButton.disabled = true;
            setGuidedStatus(workflows.length ? 'Choose the recorded request for the action you want to test, then inspect its traces. HTTP methods such as GET and POST describe how that request is sent.' : 'No operations were recorded for this service in the selected time window. Try a wider window.');
        } catch (error) { if (version === guidedConnectionVersion) setGuidedStatus(error.message, true); }
    }
    async function inspectGuidedWorkflow() {
        discoveryPoller?.stop();
        const selected = selectedGuidedWorkflow(guidedWorkflows, guidedOperation.value);
        if (!selected) return;
        const version = ++guidedConnectionVersion;
        guidedInspectButton.disabled = true;
        setGuidedStatus('Reading workflow traces…');
        try {
            const session = await postCampaignAction('/api/workflows/analysis-sessions', {...guidedConnectionPayload(), entryService: selected.rootService, operationName: selected.operationName});
            if (version !== guidedConnectionVersion) return;
            renderGuidedSession(session);
            setGuidedStatus('Workflow loaded.');
        } catch (error) { if (version === guidedConnectionVersion) setGuidedStatus(error.message, true); }
        finally { if (version === guidedConnectionVersion) guidedInspectButton.disabled = false; }
    }
    function observedGraphModel(session) {
        return {observedServices: [...session.observedServices], edges: session.edges.map(({source, target}) => ({source, target})), mandatoryServices: [], optionalServices: []};
    }
    function renderObservedGraph(session) {
        const canvas = document.createElement('div');
        renderGraph(canvas, observedGraphModel(session));
        const calls = document.createElement('ul');
        session.edges.forEach(edge => calls.append(guidedText('li', '', `${edge.source} → ${edge.target}`)));
        return details(`Recorded calls · ${session.observedServices.length} services · ${session.edges.length} connections`, guidedText('p', '', 'These calls were observed in traces. Service availability tests determine which dependencies affect success.'), canvas, details('List connections', calls));
    }
    function buildObservedWorkflowSetup(statusText, requestText, marker) {
        const codes = statusText.split(',').map(value => value.trim());
        if (!codes.every(value => /^[1-5]\d{2}$/.test(value))) throw new Error('Enter comma-separated HTTP status codes between 100 and 599.');
        const request = JSON.parse(requestText.trim() || '{}');
        if (!request || typeof request !== 'object' || Array.isArray(request)) throw new Error('Request options must be a JSON object.');
        if (request.responseContains !== undefined && !Array.isArray(request.responseContains)) throw new Error('responseContains must be a list of response markers.');
        if (marker.trim()) request.responseContains = [...(request.responseContains || []), marker.trim()];
        return {mode: 'observed_entry', expectedStatusCodes: [...new Set(codes.map(Number))], request};
    }
    function selectField(label, choices) {
        const item = field(label);
        const select = document.createElement('select');
        select.id = item.input.id;
        choices.forEach(([value, text]) => {
            const option = guidedText('option', '', text);
            option.value = value;
            select.append(option);
        });
        item.input.replaceWith(select);
        return {label: item.label, input: select};
    }
    // Collect user values only. Type conversion, validation and execution planning belong to the API.
    function inputRows(title, columns, changed) {
        const root = guidedText('div', 'request-inputs', '');
        const rows = [];
        const list = guidedText('div', 'request-rows', '');
        const add = button(`Add ${title}`, () => {
            const element = guidedText('div', 'request-row', '');
            const controls = columns.map(column => column.choices ? selectField(column.label, column.choices) : field(column.label));
            const row = {element, controls};
            element.append(...controls.map(item => item.label), button(`Remove ${title}`, () => {
                rows.splice(rows.indexOf(row), 1);
                element.remove();
                changed();
                add.focus();
            }, true));
            rows.push(row);
            list.append(element);
            controls[0].input.focus();
            changed();
        }, true);
        root.append(list, add);
        return {root, read: () => rows.map(row => Object.fromEntries(columns.map((column, index) => [column.key, row.controls[index].input.value])))};
    }
    function requestInputs(requirements, changed, pathNames = null) {
        const root = guidedText('div', 'request-editor', '');
        const pairs = [{key: 'name', label: 'Field name'}, {key: 'value', label: 'Value'}];
        let path;
        if (pathNames === null) {
            path = inputRows('path parameter', pairs, changed);
            root.append(details('Path parameters (if the path contains {name})', path.root));
        } else {
            const parameters = pathNames.map(name => ({name, ...field(`Value for {${name}}`)}));
            parameters.forEach(item => item.input.required = true);
            root.append(...parameters.map(item => item.label));
            path = {read: () => parameters.map(item => ({name: item.name, value: item.input.value}))};
        }
        const format = selectField('Request body', requirements.bodyFormats.map(value => [value, {none: 'No body', form: 'Form fields', json: 'JSON fields'}[value]]));
        const body = inputRows('body field', [...pairs, {key: 'type', label: 'Value type', choices: requirements.jsonValueTypes.map(value => [value, {text: 'Text', number: 'Number', boolean: 'True / false', null: 'Null', json: 'Object or list (JSON)'}[value]])}], changed);
        const syncBody = () => {
            body.root.hidden = format.input.value === 'none';
            body.root.querySelectorAll('select').forEach(item => { item.closest('.workspace-field').hidden = format.input.value !== 'json'; });
        };
        // Newly inserted rows also need the current body's type-selector visibility.
        body.root.addEventListener('click', syncBody);
        format.input.addEventListener('change', syncBody);
        syncBody();
        const query = inputRows('query parameter', pairs, changed);
        const headers = inputRows('header', [...pairs, {key: 'source', label: 'Value source', choices: [['literal', 'Entered value'], ['environment', 'Server environment variable']]}], changed);
        root.append(format.label, body.root, details('Query parameters (optional)', query.root), details('Headers and authentication (optional)', guidedText('p', 'workspace-help', 'For sensitive headers, choose Server environment variable and enter its variable name. Cookies are shared automatically between steps.'), headers.root));
        return {root, read: () => ({pathParameters: path.read(), queryParameters: query.read(), headers: headers.read(), bodyFormat: format.input.value, bodyFields: format.input.value === 'none' ? [] : body.read().map(row => ({...row, type: format.input.value === 'form' ? 'text' : row.type}))})};
    }
    async function renderGuidedSession(session) {
        discoveryPoller?.stop();
        const version = ++renderVersion;
        activeCampaign = null;
        setSelection(null, session.sessionId);
        $('source-panel').hidden = true;
        offlineResult.hidden = true;
        guidedResult.hidden = false;
        guidedResult.replaceChildren(guidedText('p', 'workspace-eyebrow', session.environmentId), guidedText('h3', '', session.operationName));
        sourceBadge('Workflow setup');
        const metrics = guidedText('div', 'workspace-metrics', '');
        metrics.append(resultCard('Matching traces', String(session.traceCount)), resultCard('Services observed', String(session.observedServices.length)));
        guidedResult.append(metrics, renderObservedGraph(session));
        const excluded = (session.traceQuality?.excludedCount || 0) + (session.traceQuality?.inputInvalidCount || 0);
        if (excluded || !session.observedServices.some(service => service !== session.entryService)) {
            guidedResult.append(guidedText('p', 'workspace-status', excluded ? 'Some traces were incomplete or invalid. Fix trace collection and inspect the workflow again before collecting availability tests.' : 'No downstream services were recorded. Choose a different operation or collect more traces.'), button('Choose another operation', openSource, true));
            const reasons = [...new Set((session.traceQuality?.results || []).flatMap(item => item.reasons || []))];
            if (reasons.length) guidedResult.append(details('Trace issues', ...reasons.map(reason => guidedText('p', '', reason))));
            return;
        }
        const loading = guidedText('p', 'workspace-status', 'Loading request setup…');
        guidedResult.append(loading);
        let requirements;
        try {
            requirements = await requestScenario(`/api/workflows/analysis-sessions/${encodeURIComponent(session.sessionId)}/workflow-setup`);
        } catch (error) {
            if (version === renderVersion) {
                message(loading, error.message, true);
                guidedResult.append(button('Retry setup', () => renderGuidedSession(session), true));
            }
            return;
        }
        if (version !== renderVersion) return;
        loading.remove();
        const form = guidedText('form', 'workspace-form', '');
        const name = field('Workflow name', 'text', session.operationName);
        const outcome = field('What should a successful run achieve?', 'text', '', 'Describe the result the user should receive. Configure its automated checks below.');
        const status = field('HTTP response codes that count as success', 'text', '', 'Use the codes your application returns, separated by commas. A matching trace is also required.');
        const marker = field('Text that must appear in the response (optional)');
        const criticality = field('Business importance (0–1)', 'number', '', 'Your assessment: 0 is lowest and 1 is highest.');
        Object.assign(criticality.input, {min: '0', max: '1', step: '0.01'});
        const limits = requirements.baselineLimits;
        const trials = field('Baseline runs to collect', 'number', '', `Choose ${limits.minimum}–${limits.maximum} runs with services available.`);
        Object.assign(trials.input, {min: String(limits.minimum), max: String(limits.maximum), step: '1'});
        [name, outcome, status, criticality, trials].forEach(item => item.input.required = true);
        const confirm = field('I reviewed the success checks for this workflow.', 'checkbox');
        confirm.label.className = 'checkbox-label full-width';
        confirm.input.required = true;
        confirm.input.disabled = true;
        const submit = guidedText('button', 'experiment-button', 'Save and continue to baseline runs');
        submit.type = 'submit';
        submit.disabled = true;
        const notice = guidedText('p', 'workspace-status full-width', '');
        const preview = guidedText('section', 'execution-preview full-width', '');
        preview.hidden = true;
        let revision = 0, checkedRevision = -1, checking = false;
        const invalidate = () => {
            revision += 1;
            checkedRevision = -1;
            confirm.input.checked = false;
            confirm.input.disabled = true;
            submit.disabled = true;
            preview.hidden = true;
            message(notice, 'Check the execution plan after editing your inputs.');
        };
        const action = requestInputs(requirements, invalidate, requirements.action?.pathParameters || []);
        const actionSection = guidedText('section', 'request-section full-width', '');
        actionSection.append(guidedText('h4', '', 'Request to measure'), guidedText('p', 'request-operation', requirements.action ? `${requirements.action.method} ${requirements.action.path}` : session.operationName), guidedText('p', 'workspace-help', 'The request comes from your selected operation. Add only the inputs your application needs.'), action.root, status.label, marker.label);
        const preparation = [];
        const stepList = guidedText('div', 'preparation-steps', '');
        const refreshSteps = () => preparation.forEach((item, index) => {
            item.title.textContent = `Preparation step ${index + 1}`;
            item.up.disabled = index === 0;
            item.down.disabled = index === preparation.length - 1;
            stepList.append(item.root);
        });
        const addStep = button('Add preparation step', () => {
            const root = guidedText('section', 'request-section', '');
            const title = guidedText('h5', '', '');
            const operation = field('Request (method and path)', 'text', '', 'Enter the HTTP method followed by an application path, such as GET /session.');
            const codes = field('Preparation success codes');
            operation.input.required = codes.input.required = true;
            const inputs = requestInputs(requirements, invalidate);
            const captures = inputRows('captured value', [{key: 'name', label: 'Variable name'}, {key: 'source', label: 'Read from', choices: requirements.captureSources.map(value => [value, value === 'json_field' ? 'JSON response field' : 'Response header'])}, {key: 'selector', label: 'Field path or header name'}], invalidate);
            const item = {root, title, read: () => ({operation: operation.input.value, successStatuses: codes.input.value, inputs: inputs.read(), captures: captures.read()})};
            const move = offset => {
                const index = preparation.indexOf(item);
                [preparation[index], preparation[index + offset]] = [preparation[index + offset], preparation[index]];
                refreshSteps();
                invalidate();
            };
            item.up = button('Move earlier', () => move(-1), true);
            item.down = button('Move later', () => move(1), true);
            const controls = guidedText('div', 'workspace-actions', '');
            controls.append(item.up, item.down, button('Remove step', () => {
                preparation.splice(preparation.indexOf(item), 1);
                root.remove();
                addStep.disabled = false;
                refreshSteps();
                invalidate();
                addStep.focus();
            }, true));
            root.append(title, operation.label, codes.label, inputs.root, details('Use a response value in a later step (optional)', guidedText('p', 'workspace-help', 'Capture a value here, then enter {{variableName}} in a later request. JSON field paths use dot notation.'), captures.root), controls);
            preparation.push(item);
            refreshSteps();
            addStep.disabled = preparation.length >= requirements.maximumPreparationSteps;
            invalidate();
            operation.input.focus();
        }, true);
        const preparationSection = details('Preparation steps (optional)', guidedText('p', 'workspace-help', 'Add steps only when this action needs earlier requests. They run in order before the measured request. The backend carries cookies between steps.'), stepList, addStep);
        preparationSection.className = 'full-width';
        const imported = field('Execution definition JSON', 'textarea', '', 'Import an existing reviewed definition. Ordinary requests and multi-step workflows can use the guided fields above.');
        const useImported = field('Import an execution definition (advanced)', 'checkbox');
        useImported.label.className = 'checkbox-label';
        const advanced = details('Import existing settings (advanced)', useImported.label, imported.label);
        advanced.className = 'full-width';
        const syncMode = () => {
            const enabled = useImported.input.checked;
            imported.input.disabled = !enabled;
            imported.input.required = enabled;
            imported.label.hidden = !enabled;
            [actionSection, preparationSection].forEach(section => {
                section.hidden = enabled || !requirements.guidedSupported;
                section.querySelectorAll('input, select, textarea, button').forEach(input => input.disabled = enabled || !requirements.guidedSupported);
            });
            if (!enabled) { refreshSteps(); addStep.disabled = preparation.length >= requirements.maximumPreparationSteps; }
            invalidate();
        };
        useImported.input.addEventListener('change', syncMode);
        if (!requirements.guidedSupported) {
            useImported.input.checked = true;
            advanced.open = true;
            guidedResult.append(guidedText('p', 'workspace-help', 'This is an internal operation. Choose the HTTP request that starts this action, or import a reviewed execution definition.'), button('Choose another operation', openSource, true));
        }
        syncMode();
        const readSetup = () => useImported.input.checked ? {mode: 'imported_definition', executionDefinition: JSON.parse(imported.input.value)} : {mode: 'guided_sequence', successStatuses: status.input.value, actionInputs: {...action.read(), responseText: marker.input.value}, preparationSteps: preparation.map(item => item.read())};
        const check = button('Check execution plan', async () => {
            if (checking || !form.reportValidity()) return;
            const requestedRevision = revision;
            checking = true;
            check.disabled = true;
            message(notice, 'Checking request inputs and execution order…');
            try {
                const result = await postCampaignAction(`/api/workflows/analysis-sessions/${encodeURIComponent(session.sessionId)}/workflow-probe-preview`, readSetup());
                if (version !== renderVersion || requestedRevision !== revision) return;
                const list = guidedText('ol', 'execution-steps', '');
                result.summary.forEach(step => list.append(guidedText('li', '', `${step.method} ${step.path} · success: ${step.successStatuses.join(', ')}${step.measured ? ' · measured action' : ' · preparation'}${step.capturedValues.length ? ` · saves: ${step.capturedValues.join(', ')}` : ''}`)));
                preview.replaceChildren(guidedText('h4', '', 'Execution plan'), list, guidedText('p', 'workspace-help', 'Inputs are valid. No application requests have been sent. Review the plan, then save to start baseline collection.'));
                preview.hidden = false;
                checkedRevision = revision;
                confirm.input.disabled = false;
                message(notice, 'Plan checked. Confirm the success checks to continue.');
                confirm.input.focus();
            } catch (error) { if (version === renderVersion && requestedRevision === revision) message(notice, error.message, true); }
            finally { checking = false; check.disabled = false; }
        });
        form.addEventListener('input', event => { if (event.target !== confirm.input) invalidate(); });
        form.addEventListener('change', event => {
            if (event.target === confirm.input) submit.disabled = !confirm.input.checked || checkedRevision !== revision;
            else invalidate();
        });
        const title = guidedText('h4', 'full-width', 'Set up the workflow and success checks');
        form.append(title, name.label, outcome.label, preparationSection, actionSection, criticality.label, trials.label, advanced, check, preview, confirm.label, submit, notice);
        revealInvalidFields(form);
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (checkedRevision !== revision || !confirm.input.checked || !form.reportValidity()) return;
            submit.disabled = true;
            // Lock the reviewed values while the backend saves them.
            const payload = {analysisSessionId: session.sessionId, successContractConfirmed: true, requiredBaselineTrials: Number(trials.input.value), criticality: Number(criticality.input.value), workflowName: name.input.value.trim(), businessOutcome: outcome.input.value.trim(), workflowSetup: readSetup()};
            const controls = [...form.querySelectorAll('input, select, textarea, button')];
            const disabled = controls.map(input => input.disabled);
            controls.forEach(input => input.disabled = true);
            message(notice, 'Saving workflow…');
            try {
                const campaign = await postCampaignAction('/api/workflows/experiment-campaigns', payload);
                if (version !== renderVersion) { await loadSavedWorkflows(); return; }
                try { await postCampaignAction(`${campaignUrl(campaign)}/manifest`, {}); }
                catch { /* The saved workflow offers a retry without creating a duplicate. */ }
                if (version === renderVersion) await renderCampaign(campaign);
                await loadSavedWorkflows();
            } catch (error) {
                if (version === renderVersion) {
                    message(notice, error.message, true);
                    controls.forEach((input, index) => input.disabled = disabled[index]);
                    submit.disabled = false;
                }
            }
        });
        guidedResult.append(form);
        title.tabIndex = -1;
        title.focus();
    }
    const stageLabels = {baseline_required: 'Normal runs needed', interventions_required: 'Service tests needed', ready_for_assessment: 'Ready to review', reviewed: 'Awaiting approval', approved: 'Approved'};
    function stageLabel(stage) { return stageLabels[stage] || 'Checking status'; }
    async function renderCampaign(campaign) {
        discoveryPoller?.stop();
        guidedConnectionVersion += 1;
        offlineVersion += 1;
        const version = ++renderVersion;
        activeCampaign = campaign;
        setSelection(campaign.campaignId);
        $('source-panel').hidden = true;
        offlineResult.hidden = true;
        guidedResult.hidden = false;
        guidedResult.replaceChildren(guidedText('p', 'workspace-eyebrow', campaign.environmentId), guidedText('h3', '', campaign.workflowName));
        const loading = guidedText('p', 'workspace-status', 'Loading workflow status…');
        guidedResult.append(loading);
        sourceBadge(campaign.environmentId);
        try {
            const progress = await requestScenario(`${campaignUrl(campaign)}/progress`);
            if (version !== renderVersion) return;
            loading.remove();
            const state = guidedText('div', 'workspace-state', stageLabel(progress.stage));
            const refresh = button('Refresh status', () => renderCampaign(campaign), true);
            const heading = guidedText('div', 'workspace-section-heading', '');
            heading.append(state, refresh);
            guidedResult.append(heading);
            const metrics = guidedText('div', 'workspace-metrics', '');
            metrics.append(resultCard('Normal runs', `${progress.baseline.eligibleTrials}/${progress.baseline.requiredTrials}`, `${progress.baseline.successfulTrials} successful · ${progress.baseline.failedTrials} failed · ${progress.baseline.excludedTrials} excluded`), resultCard(['approved', 'reviewed', 'ready_for_assessment'].includes(progress.stage) ? 'Services tested' : 'Services to test', String(campaign.candidateServices.length)));
            guidedResult.append(metrics, guidedText('p', '', `Success: ${campaign.successContract.outcome}`));
            if (progress.baseline.recordedTrials) guidedResult.append(renderRunHistory(campaign.baseline.batchId, 'Normal run results'));
            if (['approved', 'reviewed', 'ready_for_assessment'].includes(progress.stage)) {
                const decision = guidedText('section', 'assessment-console', '');
                guidedResult.append(decision);
                await loadCampaignDecision(campaign, decision, progress.interventionEvidence);
            } else {
                const manifests = await requestScenario('/api/workflows/experiment-manifests');
                if (version !== renderVersion) return;
                const manifest = manifests.find(item => item.campaignId === campaign.campaignId);
                if (!manifest) renderSaveSettings(campaign);
                else if (!manifest.integrityVerified) guidedResult.append(guidedText('p', 'is-error', 'The saved test settings could not be verified. Create a new workflow before collecting runs.'));
                else if (!progress.baseline.ready) renderBaseline(campaign, progress);
                else {
                    const console = guidedText('section', 'intervention-console', '');
                    guidedResult.append(console);
                    await loadInterventionConsole(campaign, console);
                }
            }
            if (version !== renderVersion) return;
            const observed = guidedText('div', '', '');
            guidedResult.append(observed);
            requestScenario(`/api/workflows/analysis-sessions/${encodeURIComponent(campaign.analysisSessionId)}`).then(session => { if (version === renderVersion) observed.replaceWith(renderObservedGraph(session)); }).catch(error => { if (version === renderVersion) message(observed, `Recorded calls unavailable: ${error.message}`, true); });
        } catch (error) {
            if (version !== renderVersion) return;
            message(loading, error.message, true);
            if (!loading.isConnected) guidedResult.append(loading);
            guidedResult.append(button('Retry', () => renderCampaign(campaign), true));
        }
    }
    function renderRunHistory(batchId, title) {
        const status = guidedText('p', 'workspace-status', '');
        const panel = details(title, status);
        let loaded = false;
        panel.addEventListener('toggle', async () => {
            if (!panel.open || loaded) return;
            loaded = true;
            message(status, 'Loading run results…');
            try {
                const runs = await requestScenario(`/api/workflows/executions?batch_id=${encodeURIComponent(batchId)}`);
                if (!panel.isConnected) return;
                message(status, runs.length ? `${runs.length} recorded runs` : 'No runs have been recorded.');
                const list = document.createElement('ul');
                runs.forEach(run => list.append(guidedText('li', '', `${new Date(run.recordedAt).toLocaleString()} · ${run.status} · trace ${run.correlationStatus}${run.reasons.length ? ': ' + run.reasons.join(' ') : ''}`)));
                panel.append(list);
            } catch (error) { loaded = false; message(status, error.message, true); }
        });
        return panel;
    }
    function renderSaveSettings(campaign) {
        const notice = guidedText('p', 'workspace-status', '');
        const save = button('Confirm test settings', async () => {
            save.disabled = true;
            try { await postCampaignAction(`${campaignUrl(campaign)}/manifest`, {}); if (activeCampaign?.campaignId === campaign.campaignId) await renderCampaign(campaign); }
            catch (error) { message(notice, error.message, true); save.disabled = false; }
        });
        guidedResult.append(guidedText('h4', '', 'Ready to collect normal runs'), guidedText('p', '', 'Confirm the saved workflow settings before running the application.'), save, notice);
    }
    function applicationField(campaign) {
        const item = field('Application base URL', 'url', stored(`workflow-application:${campaign.campaignId}`) || '', 'Enter the address of the application being tested, including http:// or https://. Use its base address, not the tracing server or a page path.');
        item.input.required = true;
        item.input.addEventListener('change', () => { if (item.input.checkValidity()) remember(`workflow-application:${campaign.campaignId}`, item.input.value.trim()); });
        return item;
    }
    function renderBaseline(campaign, progress) {
        const form = guidedText('form', 'baseline-console workspace-form', '');
        const app = applicationField(campaign);
        const submit = guidedText('button', 'experiment-button', 'Run one baseline test');
        submit.type = 'submit';
        const notice = guidedText('p', 'workspace-status full-width', '');
        form.append(guidedText('h4', 'full-width', 'Collect a baseline with services available'), guidedText('p', 'full-width', `Each click executes the configured workflow once. Collect ${progress.baseline.requiredTrials} runs matched to traces. The baseline must also pass the stability checks before service availability tests.`), app.label, submit, notice);
        if (progress.baseline.blockers.length) form.append(details('What is still needed', ...progress.baseline.blockers.map(text => guidedText('p', '', text))));
        if (progress.baseline.excludedReasons?.length) form.append(details('Excluded runs', ...progress.baseline.excludedReasons.map(text => guidedText('p', '', text))));
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (!form.reportValidity()) return;
            submit.disabled = true;
            message(notice, 'Running workflow and finding its trace…');
            try {
                const result = await postCampaignAction(`${campaignUrl(campaign)}/baseline-runs`, {applicationUrl: app.input.value.trim()});
                remember(`workflow-application:${campaign.campaignId}`, app.input.value.trim());
                if (result.correlationStatus !== 'matched') { message(notice, `Run recorded but excluded: trace match is ${result.correlationStatus}. Refresh status to inspect the counts.`, true); submit.disabled = false; }
                else if (activeCampaign?.campaignId === campaign.campaignId) await renderCampaign(campaign);
            } catch (error) { message(notice, error.message, true); submit.disabled = false; }
        });
        guidedResult.append(form);
    }
    async function loadInterventionConsole(campaign, target) {
        target.replaceChildren(guidedText('h4', '', 'Service availability tests'), guidedText('p', '', 'Record an approved service outage, run the workflow, then record recovery. These controls record evidence; they do not stop or restart services.'));
        const app = applicationField(campaign);
        target.append(app.label);
        try {
            const status = await requestScenario(`${campaignUrl(campaign)}/intervention-status`);
            if (!target.isConnected) return;
            const active = status.services.find(service => service.state === 'active');
            status.services.forEach(service => target.append(renderInterventionService(campaign, service, app.input, active?.targetService)));
        } catch (error) { target.append(guidedText('p', 'is-error', error.message)); }
    }
    function renderInterventionService(campaign, service, applicationUrl, activeTarget) {
        const card = details('', guidedText('p', '', `${service.eligibleTrials}/${service.requiredTrials} usable runs · ${service.excludedTrials} excluded`));
        card.className = 'intervention-service';
        card.querySelector('summary').append(guidedText('strong', '', service.targetService), guidedText('span', 'workspace-state', service.evidenceReady ? 'Complete' : service.state === 'active' ? 'Outage recorded' : service.state === 'restored' ? 'Restored · more runs needed' : 'Not tested'));
        card.open = service.state === 'active';
        if (service.trialCount) card.append(renderRunHistory(service.batchId, 'Service test runs'));
        if (service.evidenceReady) { card.append(guidedText('p', '', 'Test results recorded and service restored.')); return card; }
        const evidence = field(service.state === 'active' ? 'Recovery evidence' : 'Outage evidence', 'textarea');
        const notice = guidedText('p', 'workspace-status', '');
        const controls = guidedText('div', 'workspace-actions', '');
        const eventButton = button(service.state === 'active' ? 'Record recovery' : 'Record outage', async () => {
            if (!evidence.input.value.trim()) { evidence.input.setCustomValidity('Enter the observation or command result confirming the service state.'); evidence.input.reportValidity(); return; }
            eventButton.disabled = true;
            try {
                await postCampaignAction(`${campaignUrl(campaign)}/intervention-events`, {targetService: service.targetService, eventType: service.state === 'active' ? 'restored' : 'activated', operatorConfirmed: true, evidence: evidence.input.value.trim(), occurredAt: new Date().toISOString()});
                if (activeCampaign?.campaignId === campaign.campaignId) await renderCampaign(campaign);
            } catch (error) { message(notice, error.message, true); eventButton.disabled = false; }
        }, service.state === 'active');
        evidence.input.addEventListener('input', () => evidence.input.setCustomValidity(''));
        if (service.state === 'active') {
            const run = button('Run workflow trial', async () => {
                if (!applicationUrl.reportValidity()) return;
                run.disabled = true;
                message(notice, 'Running workflow…');
                try { await postCampaignAction(`${campaignUrl(campaign)}/interventions/${encodeURIComponent(service.targetService)}/runs`, {applicationUrl: applicationUrl.value.trim()}); if (activeCampaign?.campaignId === campaign.campaignId) await renderCampaign(campaign); }
                catch (error) { message(notice, error.message, true); run.disabled = false; }
            });
            controls.append(run);
        }
        if (activeTarget && service.state !== 'active') { eventButton.disabled = true; message(notice, `Record recovery for ${activeTarget} before starting another service test.`); }
        controls.append(eventButton);
        card.append(evidence.label, controls, notice);
        if (service.excludedReasons?.length) card.append(details('Excluded runs', ...service.excludedReasons.map(text => guidedText('p', '', text))));
        return card;
    }
    async function loadCampaignDecision(campaign, target, verification) {
        try { const approval = await requestScenario(`${campaignUrl(campaign)}/approval`); if (target.isConnected) renderApprovedGraph(approval, target, verification); return; }
        catch (error) { if (error.status !== 404) { message(target, error.message, true); return; } }
        try { const assessment = await requestScenario(`${campaignUrl(campaign)}/assessment`); if (target.isConnected) renderCampaignAssessment(campaign, assessment, target); }
        catch (error) {
            if (!target.isConnected) return;
            if (error.status !== 404) { message(target, error.message, true); return; }
            const notice = guidedText('p', 'workspace-status', '');
            const calculate = button('Review dependency results', async () => {
                calculate.disabled = true;
                message(notice, 'Comparing workflow outcomes…');
                try { const assessment = await postCampaignAction(`${campaignUrl(campaign)}/assessment`, {}); if (target.isConnected) renderCampaignAssessment(campaign, assessment, target); }
                catch (error) { message(notice, error.message, true); calculate.disabled = false; }
            });
            target.append(guidedText('h4', '', 'Service tests complete'), calculate, notice);
        }
    }
    function assessmentRows(assessment) {
        const table = guidedText('table', 'workspace-table', '');
        const header = document.createElement('thead');
        const heading = document.createElement('tr');
        ['Service', 'Dependency', 'Normal successes', 'Outage successes'].forEach(text => heading.append(guidedText('th', '', text)));
        header.append(heading);
        const body = document.createElement('tbody');
        assessment.services.forEach(service => {
            const row = document.createElement('tr');
            const count = cohort => `${cohort.succeededRecords}/${cohort.eligibleRecords}`;
            [service.serviceName, service.classification.replaceAll('_', ' '), count(service.baseline), count(service.intervention)].forEach(text => row.append(guidedText('td', '', text)));
            body.append(row);
        });
        table.append(header, body);
        const wrap = guidedText('div', 'workspace-table-scroll', '');
        wrap.append(table);
        return wrap;
    }
    function classificationKey() { return guidedText('p', 'workspace-help', 'Mandatory: needed for success. Supporting: affects some outcomes. Optional: no material effect in the recorded tests.'); }
    function renderCampaignAssessment(campaign, assessment, target) {
        target.replaceChildren(guidedText('h4', '', 'Review dependencies'), classificationKey(), assessmentRows(assessment));
        target.append(details('Result details', ...assessment.services.map(service => guidedText('p', '', `${service.serviceName}: ${service.reasons.join(' ')}`))));
        const form = guidedText('form', 'workspace-form', '');
        const actor = field('Reviewer name');
        const rationale = field('Review note', 'textarea');
        const confirm = field('I reviewed these results and approve this graph.', 'checkbox');
        confirm.label.className = 'checkbox-label full-width';
        [actor, rationale, confirm].forEach(item => item.input.required = true);
        const submit = guidedText('button', 'experiment-button', 'Approve graph');
        submit.type = 'submit';
        const notice = guidedText('p', 'workspace-status full-width', '');
        const unresolved = assessment.services.some(item => ['conditional', 'insufficient_evidence'].includes(item.classification));
        if (unresolved) { submit.disabled = true; message(notice, 'Resolve dependencies with insufficient or condition-specific evidence before approval.', true); }
        form.append(actor.label, rationale.label, confirm.label, submit, notice);
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (!form.reportValidity() || unresolved) return;
            submit.disabled = true;
            try { await postCampaignAction(`${campaignUrl(campaign)}/approval`, {rationale: rationale.input.value.trim(), approvalConfirmed: true}, actor.input.value.trim()); if (activeCampaign?.campaignId === campaign.campaignId) await renderCampaign(campaign); }
            catch (error) { message(notice, error.message, true); submit.disabled = false; }
        });
        target.append(form);
    }
    function renderApprovedGraph(approval, target, verification) {
        const graph = approval.approvedGraph;
        target.replaceChildren(guidedText('h4', '', 'Approved dependency graph'));
        const groups = guidedText('div', 'workspace-metrics', '');
        groups.append(resultCard('Mandatory', graph.mandatoryServices.join(', ') || 'None'), resultCard('Supporting', (approval.supportingServices || approval.excludedSupportingServices || []).join(', ') || 'None'), resultCard('Optional', graph.optionalServices.join(', ') || 'None'));
        const canvas = document.createElement('div');
        renderGraph(canvas, graph);
        target.append(groups, classificationKey(), canvas, guidedText('p', 'workspace-help', 'This graph shows mandatory and optional dependencies. Supporting services are included in the test results. Arrows show recorded calls; the entry service is included as the workflow entry point.'), button('Download graph JSON', () => downloadJsonArtifact(`${graph.workflowId}-workflow-graph.json`, graph)), guidedText('p', '', `Reviewed by ${approval.approvedBy}: ${approval.rationale}`));
        if (verification) target.append(guidedText('p', 'workspace-help', `Outage verification: ${verification.verifiedTrials}/${verification.eligibleTrials} runs verified by the backend; ${verification.verification.replaceAll('_', ' ')}.`));
        const evidence = details('View test results', guidedText('p', '', 'Loading saved results…'));
        target.append(evidence);
        requestScenario(`/api/workflows/dependency-assessments/${encodeURIComponent(approval.assessmentId)}`).then(assessment => {
            if (!target.isConnected) return;
            if (assessment.assessmentId !== approval.assessmentId || assessment.workflowId !== approval.workflowId) throw new Error('The assessment does not match this graph.');
            evidence.replaceChildren(guidedText('summary', '', 'View test results'), assessmentRows(assessment), button('Download test results JSON', () => downloadJsonArtifact(`${graph.workflowId}-test-results.json`, assessment), true));
        }).catch(error => { if (target.isConnected) evidence.append(guidedText('p', 'is-error', error.message)); });
    }
    function revealInvalidFields(form) {
        form.addEventListener('invalid', event => { for (let parent = event.target.parentElement; parent && parent !== form; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true; }, true);
    }
    async function analyzeUploadedWorkflow(event) {
        event.preventDefault();
        if (!offlineForm.reportValidity()) return;
        const version = ++offlineVersion;
        const submit = offlineForm.querySelector('button[type="submit"]');
        submit.disabled = true;
        offlineResult.hidden = true;
        message(offlineStatus, 'Analyzing uploaded traces…');
        try {
            const input = name => offlineForm.elements.namedItem(name);
            let definition;
            if (input('useDefinition').checked) {
                try { definition = JSON.parse(input('workflowDefinition').value); } catch (error) { throw new Error(`Workflow definition JSON: ${error.message}`); }
            } else {
                const setup = buildObservedWorkflowSetup(input('statuses').value, '', '');
                const id = input('workflowName').value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '');
                if (!id) throw new Error('Use letters or numbers in the workflow name, or supply an existing workflow definition.');
                definition = {workflowId: id, description: input('outcome').value.trim(), entryService: input('entryService').value.trim(), criticality: Number(input('criticality').value), successCondition: {protocol: 'http', terminalService: input('entryService').value.trim(), operationName: input('operationName').value.trim(), httpStatusCodes: setup.expectedStatusCodes}};
            }
            let traces;
            try { traces = JSON.parse(await input('traces').files[0].text()); } catch (error) { throw new Error(`Trace export JSON: ${error.message}`); }
            const result = await postCampaignAction('/api/workflows/analyze', {workflowDefinition: definition, operationName: input('operationName').value.trim(), sourceType: input('sourceType').value, traces, mandatoryThreshold: Number(input('mandatoryThreshold').value), minEdgeFrequency: Number(input('minEdgeFrequency').value)});
            if (version !== offlineVersion) return;
            const summary = guidedText('div', 'workspace-metrics', '');
            summary.append(resultCard('Successful', String(result.successfulTraceCount)), resultCard('Failed', String(result.failedTraceCount)), resultCard('Unknown outcome', String(result.indeterminateTraceCount)), resultCard('Excluded', String(result.traceQuality.excludedCount)));
            offlineResult.replaceChildren(guidedText('h3', '', 'Trace analysis'), summary, guidedText('p', '', result.candidateGraph ? 'Observed dependencies are ready to inspect. Availability tests are needed before these can be approved.' : 'Insufficient evidence: no matching successful traces were available.'));
            if (result.candidateGraph) {
                const canvas = document.createElement('div');
                renderGraph(canvas, {observedServices: result.serviceEvidence.map(item => item.service), edges: result.candidateGraph.edges, mandatoryServices: [], optionalServices: []});
                offlineResult.append(canvas);
            }
            const list = document.createElement('ul');
            result.serviceEvidence.forEach(item => list.append(guidedText('li', '', `${item.service}: ${item.traceCount}/${result.successfulTraceCount} successful traces (${(item.frequency * 100).toFixed(1)}%) · ${item.classification.replaceAll('_', ' ').toLowerCase()}`)));
            offlineResult.append(list, details('Analysis details', ...result.limitations.map(text => guidedText('p', '', text)), guidedText('pre', '', JSON.stringify(result, null, 2))), button('Download analysis JSON', () => downloadJsonArtifact(`${result.workflowId}-analysis.json`, result), true));
            offlineResult.hidden = false;
            message(offlineStatus, `${result.matchedTraceCount} matching traces analyzed. ${result.unrelatedTraceCount} traces belonged to other workflows.`);
        } catch (error) { if (version === offlineVersion) message(offlineStatus, error.message, true); }
        finally { submit.disabled = false; }
    }
    function renderGraph(container, workflow) {
        container.innerHTML = "";
        container.className = "workflow-dag-container";
        container.tabIndex = 0;
        container.setAttribute("role", "region");
        container.setAttribute("aria-label", "Scrollable service call graph");

        const edges = workflow.edges || [];
        const mandatorySet = new Set(workflow.mandatoryServices || []);
        const optionalSet = new Set(workflow.optionalServices || []);

        // Collect all unique nodes
        const allNodesSet = new Set([
            ...(workflow.observedServices || []),
            ...workflow.mandatoryServices,
            ...workflow.optionalServices,
            ...edges.map((e) => e.source),
            ...edges.map((e) => e.target),
        ]);
        const allNodes = Array.from(allNodesSet);

        if (!allNodes.length) {
            container.innerHTML = '<div class="muted">No services found in graph</div>';
            return;
        }

        // Build adjacency and in-degree maps
        const adj = new Map();
        const inDegree = new Map();
        allNodes.forEach((n) => {
            adj.set(n, []);
            inDegree.set(n, 0);
        });

        edges.forEach((e) => {
            if (adj.has(e.source) && adj.has(e.target)) {
                adj.get(e.source).push(e.target);
                inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
            }
        });

        // Determine topological ranks (levels) via BFS from roots (inDegree === 0)
        const nodeRank = new Map();
        const queue = [];

        allNodes.forEach((n) => {
            if ((inDegree.get(n) || 0) === 0) {
                nodeRank.set(n, 0);
                queue.push(n);
            }
        });

        // If cyclic or no 0-indegree, pick first node as root
        if (!queue.length && allNodes.length) {
            nodeRank.set(allNodes[0], 0);
            queue.push(allNodes[0]);
        }

        while (queue.length > 0) {
            const curr = queue.shift();
            const currRank = nodeRank.get(curr) || 0;
            const neighbors = adj.get(curr) || [];

            neighbors.forEach((nbr) => {
                const existing = nodeRank.get(nbr);
                if (existing === undefined) {
                    nodeRank.set(nbr, currRank + 1);
                    queue.push(nbr);
                }
            });
        }

        // Fallback for any unranked nodes
        allNodes.forEach((n) => {
            if (!nodeRank.has(n)) nodeRank.set(n, 1);
        });

        // Group nodes by level
        const levels = [];
        allNodes.forEach((n) => {
            const r = nodeRank.get(n) || 0;
            while (levels.length <= r) levels.push([]);
            levels[r].push(n);
        });

        // Layout dimensions
        const nodeW = Math.max(160, ...allNodes.map(name => name.length * 7 + 24));
        const nodeH = 46;
        const colGap = 70;
        const rowGap = 24;

        const maxLevelSize = Math.max(...levels.map((l) => l.length), 1);
        const svgW = Math.max(levels.length * (nodeW + colGap) + 40, 480);
        const svgH = Math.max(maxLevelSize * (nodeH + rowGap) + 40, 180);

        // Compute (x, y) for each node centered vertically within its level
        const positions = new Map();
        levels.forEach((levelNodes, colIdx) => {
            const colX = 20 + colIdx * (nodeW + colGap);
            const totalColH = levelNodes.length * nodeH + (levelNodes.length - 1) * rowGap;
            const startY = (svgH - totalColH) / 2;

            levelNodes.forEach((nodeName, rowIdx) => {
                const y = startY + rowIdx * (nodeH + rowGap);
                positions.set(nodeName, { x: colX, y: y });
            });
        });

        // Build SVG
        const ns = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(ns, "svg");
        svg.setAttribute("role", "img");
        svg.setAttribute("aria-label", "Service call graph. Connections are also available in the recorded calls list.");
        svg.style.width = `${svgW}px`;
        svg.setAttribute("viewBox", `0 0 ${svgW} ${svgH}`);
        svg.classList.add("workflow-dag-svg");

        // Defs (Arrowheads)
        const defs = document.createElementNS(ns, "defs");
        defs.innerHTML = `
            <marker id="dag-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#94a3b8"></path>
            </marker>
            <marker id="dag-arrow-mandatory" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#6366f1"></path>
            </marker>
            <marker id="dag-arrow-active" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#2563eb"></path>
            </marker>
        `;
        svg.appendChild(defs);

        // Edges group
        const edgesGroup = document.createElementNS(ns, "g");
        edgesGroup.classList.add("dag-edges-layer");

        edges.forEach((edge) => {
            const p1 = positions.get(edge.source);
            const p2 = positions.get(edge.target);
            if (!p1 || !p2) return;

            const startX = p1.x + nodeW;
            const startY = p1.y + nodeH / 2;
            const endX = p2.x;
            const endY = p2.y + nodeH / 2;

            const isMandatory = mandatorySet.has(edge.target);
            const isOptional = optionalSet.has(edge.target);
            const strokeColor = isMandatory ? "#6366f1" : isOptional ? "#f59e0b" : "#94a3b8";
            const markerId = isMandatory ? "dag-arrow-mandatory" : "dag-arrow";

            const path = document.createElementNS(ns, "path");
            const cX = (startX + endX) / 2;
            path.setAttribute("d", `M ${startX} ${startY} C ${cX} ${startY}, ${cX} ${endY}, ${endX} ${endY}`);
            path.setAttribute("stroke", strokeColor);
            path.setAttribute("stroke-width", "2");
            path.setAttribute("fill", "none");
            if (isOptional) path.setAttribute("stroke-dasharray", "5,4");
            path.setAttribute("class", "dag-edge-path");
            path.setAttribute("marker-end", `url(#${markerId})`);
            path.dataset.source = edge.source;
            path.dataset.target = edge.target;
            path.dataset.mandatory = isMandatory ? "true" : "false";

            edgesGroup.appendChild(path);
        });
        svg.appendChild(edgesGroup);

        // Nodes group
        const nodesGroup = document.createElementNS(ns, "g");
        nodesGroup.classList.add("dag-nodes-layer");

        allNodes.forEach((nodeName) => {
            const pos = positions.get(nodeName);
            if (!pos) return;

            const isEntry = (inDegree.get(nodeName) || 0) === 0;
            const isMandatory = mandatorySet.has(nodeName);
            const isOptional = optionalSet.has(nodeName);

            let fillColor = "#eef2ff";
            let strokeColor = "#6366f1";
            let badgeColor = "#4f46e5";
            let badgeText = "MANDATORY";
            let dashed = false;

            if (isEntry && !isMandatory) {
                fillColor = "#eff6ff";
                strokeColor = "#3b82f6";
                badgeColor = "#2563eb";
                badgeText = "ENTRY";
            } else if (isOptional) {
                fillColor = "#fffbeb";
                strokeColor = "#f59e0b";
                badgeColor = "#b45309";
                badgeText = "OPTIONAL";
                dashed = true;
            } else if (isMandatory) {
                fillColor = "#eef2ff";
                strokeColor = "#6366f1";
                badgeColor = "#4f46e5";
                badgeText = "MANDATORY";
            } else {
                fillColor = "#f8fafc";
                strokeColor = "#94a3b8";
                badgeColor = "#64748b";
                badgeText = "SERVICE";
            }

            const g = document.createElementNS(ns, "g");
            g.setAttribute("class", "dag-node");
            g.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);
            g.dataset.service = nodeName;

            const rect = document.createElementNS(ns, "rect");
            rect.setAttribute("width", String(nodeW));
            rect.setAttribute("height", String(nodeH));
            rect.setAttribute("rx", "8");
            rect.setAttribute("fill", fillColor);
            rect.setAttribute("stroke", strokeColor);
            rect.setAttribute("stroke-width", "1.5");
            rect.setAttribute("class", "dag-node-rect");
            if (dashed) rect.setAttribute("stroke-dasharray", "4,3");

            const nameText = document.createElementNS(ns, "text");
            nameText.setAttribute("x", "12");
            nameText.setAttribute("y", "20");
            nameText.setAttribute("fill", "#0f172a");
            nameText.setAttribute("font-size", "12");
            nameText.setAttribute("font-weight", "700");
            nameText.setAttribute("font-family", "system-ui, -apple-system, sans-serif");
            nameText.textContent = nodeName;

            const badge = document.createElementNS(ns, "text");
            badge.setAttribute("x", "12");
            badge.setAttribute("y", "36");
            badge.setAttribute("fill", badgeColor);
            badge.setAttribute("font-size", "10");
            badge.setAttribute("font-weight", "700");
            badge.setAttribute("font-family", "system-ui, -apple-system, sans-serif");
            badge.textContent = badgeText;
            g.append(rect, nameText, badge);

            // Hover interactions
            g.addEventListener("mouseenter", () => {
                svg.querySelectorAll(".dag-edge-path").forEach((p) => {
                    if (p.dataset.source === nodeName || p.dataset.target === nodeName) {
                        p.setAttribute("stroke", "#2563eb");
                        p.setAttribute("stroke-width", "3.5");
                        p.style.opacity = "1";
                        p.setAttribute("marker-end", "url(#dag-arrow-active)");
                    } else {
                        p.style.opacity = "0.2";
                    }
                });

                svg.querySelectorAll(".dag-node").forEach((n) => {
                    const s = n.dataset.service;
                    const isConnected =
                        s === nodeName ||
                        edges.some(
                            (e) =>
                                (e.source === nodeName && e.target === s) ||
                                (e.target === nodeName && e.source === s)
                        );
                    n.style.opacity = isConnected ? "1" : "0.3";
                });
            });

            g.addEventListener("mouseleave", () => {
                svg.querySelectorAll(".dag-edge-path").forEach((p) => {
                    const isMand = p.dataset.mandatory === "true";
                    p.setAttribute("stroke", isMand ? "#6366f1" : "#94a3b8");
                    p.setAttribute("stroke-width", "2");
                    p.style.opacity = "1";
                    p.setAttribute("marker-end", `url(#${isMand ? "dag-arrow-mandatory" : "dag-arrow"})`);
                });
                svg.querySelectorAll(".dag-node").forEach((n) => {
                    n.style.opacity = "1";
                });
            });

            nodesGroup.appendChild(g);
        });


        svg.appendChild(nodesGroup);
        container.appendChild(svg);
    }



    if (!guidedForm) return;
    guidedForm.addEventListener('submit', connectGuidedSource);
    $('guided-auto-discover').addEventListener('click', findWorkflowsAutomatically);
    document.addEventListener('visibilitychange', () => discoveryPoller?.pageVisibilityChanged());
    window.addEventListener('focus', () => discoveryPoller?.pageVisibilityChanged());
    guidedForm.addEventListener('input', event => {
        if (event.target.name === 'entryService') return;
        resetGuidedDiscovery();
        syncGuidedAuthentication();
        setGuidedStatus('Connection settings changed. Connect to load services.');
    });
    guidedService.addEventListener('change', () => { if (guidedService.value) discoverGuidedWorkflows(); else { guidedDiscovery.hidden = true; guidedResult.hidden = true; } });
    guidedOperation.addEventListener('change', () => {
        if (!discoveryPoller?.isRunning()) guidedConnectionVersion += 1;
        const selected = selectedGuidedWorkflow(guidedWorkflows, guidedOperation.value);
        guidedInspectButton.disabled = !selected;
        const help = $('guided-operation-help');
        if (help) help.textContent = selected ? describeOperation(selected.operationName).text : 'Choose the action you want to test. Prefer a request from the service that receives user traffic.';
    });
    guidedInspectButton.addEventListener('click', inspectGuidedWorkflow);
    $('new-workflow').addEventListener('click', openSource);
    $('refresh-saved').addEventListener('click', () => loadSavedWorkflows());
    document.querySelectorAll('[data-source]').forEach(item => item.addEventListener('click', () => {
        resetGuidedDiscovery();
        offlineVersion += 1;
        const live = item.dataset.source === 'live';
        $('live-source').hidden = !live;
        $('upload-source').hidden = live;
        guidedResult.hidden = true;
        offlineResult.hidden = true;
        document.querySelectorAll('[data-source]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === item)));
    }));
    offlineForm.addEventListener('submit', analyzeUploadedWorkflow);
    offlineForm.elements.useDefinition.addEventListener('change', () => {
        const enabled = offlineForm.elements.useDefinition.checked;
        offlineForm.elements.workflowDefinition.disabled = !enabled;
        offlineForm.elements.workflowDefinition.required = enabled;
        ['workflowName', 'entryService', 'outcome', 'statuses', 'criticality'].forEach(name => { offlineForm.elements.namedItem(name).disabled = enabled; });
        offlineResult.hidden = true;
    });
    offlineForm.addEventListener('input', () => { offlineVersion += 1; offlineResult.hidden = true; message(offlineStatus, 'Inputs changed. Analyze again to update the results.'); });
    revealInvalidFields(guidedForm);
    revealInvalidFields(offlineForm);
    const preferences = stored(preferencesKey);
    if (preferences) Object.entries({environmentId: preferences.environmentId, evidenceTier: preferences.evidenceTier, traceSourceType: preferences.sourceType, traceEndpoint: preferences.endpoint, lookback: preferences.lookback}).forEach(([name, value]) => { if (typeof value === 'string') guidedForm.elements.namedItem(name).value = value; });
    syncGuidedAuthentication();
    $('source-title').tabIndex = -1;
    sourceBadge('No workflow selected');
    loadSavedWorkflows(true);
})();
