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
    let workflowCatalog = [];
    let savedLoadVersion = 0;
    const savedLoadTimeoutMs = 30000;
    let offlineVersion = 0;
    let fieldCounter = 0;
    let discoveryPoller = null;
    let cancelActiveScan = null;
    function stopDiscoveryScan() {
        discoveryPoller?.stop();
        cancelActiveScan?.();
        cancelActiveScan = null;
        $('stop-workflow-scan').hidden = true;
    }
    let traceActionBusy = false;
    let traceUnavailable = false;
    let traceControlsChecked = false;
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
    function setSelection(id, inspectionId = null, view = 'collect') {
        const url = new URL(location.href);
        if (id) url.searchParams.set('workflow', id); else url.searchParams.delete('workflow');
        if (inspectionId) url.searchParams.set('inspection', inspectionId); else url.searchParams.delete('inspection');
        if (id) url.searchParams.set('view', view); else url.searchParams.delete('view');
        history.replaceState(null, '', url);
        $('saved-workflows').value = id || '';
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
    // The application to exercise: the address last used here, else where the latest inspected traces show it was
    // reached. Nothing about any particular application is assumed.
    const applicationKey = 'workflow-application-last';
    function browserAddress(url) {
        try {
            const parsed = new URL(url);
            // The backend reaches the host as host.docker.internal; the person's browser reaches it as localhost.
            if (parsed.hostname === 'host.docker.internal') parsed.hostname = 'localhost';
            return parsed.toString().replace(/\/$/, '');
        } catch { return ''; }
    }
    async function knownApplicationAddress() {
        const saved = stored(applicationKey);
        if (typeof saved === 'string' && saved) return {url: saved, source: 'Last address used here.'};
        try {
            const sessions = await requestScenario('/api/workflows/analysis-sessions');
            const latest = [...sessions].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
                .find(item => item.rootRequestEvidence?.suggestedApplicationUrl);
            if (latest) {
                const suggestion = latest.rootRequestEvidence.suggestedApplicationUrl;
                return {url: suggestion.recordedUrl, source: `From recorded traces: ${suggestion.basis}.`};
            }
        } catch { /* Without a suggestion the person enters the address. */ }
        return null;
    }
    function openApplication(url) {
        const address = browserAddress(url);
        if (!address) return;
        remember(applicationKey, address);
        // Inside Docker Desktop the page cannot open windows itself; the SDK opens the system browser.
        if (window.ddClient?.host?.openExternal) window.ddClient.host.openExternal(address);
        else window.open(address, '_blank', 'noopener');
    }
    async function collectNewEvidence() {
        openSource();
        const input = $('app-url');
        if (!input.value) {
            const known = await knownApplicationAddress();
            if (known) { input.value = browserAddress(known.url); message($('app-url-source'), known.source); }
        }
        if (input.value) openApplication(input.value);
        else { message($('app-url-source'), 'Enter your application address and open it to record new traces.', true); input.focus(); }
    }
    async function loadSavedWorkflows(resume = false) {
        const initialVersion = renderVersion;
        // Only the latest load may write: a request held by Docker Desktop while the backend restarted can fail
        // minutes later, and its error must not replace a list a newer load already filled.
        const load = ++savedLoadVersion;
        const status = $('saved-status');
        const list = $('saved-workflows');
        message(status, 'Loading experiment data…');
        try {
            const read = endpoint => requestScenario(endpoint, {signal: AbortSignal.timeout(savedLoadTimeoutMs)});
            const [campaigns, assessments] = await Promise.all([
                read('/api/workflows/experiment-campaigns'),
                read('/api/workflows/dependency-assessments'),
            ]);
            if (load !== savedLoadVersion) return;
            workflowCatalog = campaigns;
            list.replaceChildren(new Option('Choose an experiment', ''));
            const recorded = campaign => assessments.some(item => item.assessmentId === `assessment:${campaign.campaignId.split(':').at(-1)}` && item.workflowId === campaign.workflowId && item.environmentId === campaign.environmentId);
            // The options already say which datasets are classified; the line only speaks when there is nothing to choose.
            message(status, campaigns.length ? '' : 'No experiment data yet. Collect new evidence or upload a trace export.');
            campaigns.forEach(campaign => {
                // Date, run count and service count tell apart experiments of the same workflow and environment.
                const created = campaign.createdAt ? new Date(campaign.createdAt).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) : '';
                const size = `${campaign.baseline?.requiredTrials ?? '?'} runs · ${(campaign.candidateServices || []).length} services`;
                list.append(new Option(`${campaign.workflowName} · ${created} · ${size} · ${recorded(campaign) ? 'classified' : 'observations only'}`, campaign.campaignId));
            });
            list.value = activeCampaign?.campaignId || '';
            if (resume && initialVersion === renderVersion) {
                const params = new URL(location.href).searchParams;
                const id = params.get('workflow');
                const inspectionId = params.get('inspection');
                if (!id && inspectionId) {
                    const session = await requestScenario(`/api/workflows/analysis-sessions/${encodeURIComponent(inspectionId)}`);
                    if (initialVersion === renderVersion) renderGuidedSession(session);
                }
                if (params.get('view') === 'collect' && id) {
                    const selected = campaigns.find(item => item.campaignId === id);
                    if (selected) await renderCampaign(selected);
                    else message(status, 'This experiment is unavailable. Choose another dataset.', true);
                } else if (!inspectionId) {
                    const preferred = params.get('view') === 'recorded' ? id : stored('workflow-recorded-selection');
                    const selected = campaigns.find(item => item.campaignId === preferred) || campaigns.find(recorded) || campaigns.find(item => item.campaignId === id);
                    if (selected) await renderRecordedCampaign(selected);
                }
            }
        } catch (error) {
            if (load !== savedLoadVersion) return;
            const reason = error.name === 'TimeoutError' ? 'the backend did not answer in time. Press Refresh to try again.' : error.message;
            message(status, `Could not load experiment data: ${reason}`, true);
        }
    }
    async function renderRecordedCampaign(campaign) {
        stopDiscoveryScan();
        guidedConnectionVersion += 1;
        offlineVersion += 1;
        const version = ++renderVersion;
        activeCampaign = campaign;
        setSelection(campaign.campaignId, null, 'recorded');
        remember('workflow-recorded-selection', campaign.campaignId);
        $('source-panel').hidden = true;
        offlineResult.hidden = true;
        guidedResult.hidden = false;
        guidedResult.replaceChildren(guidedText('h3', '', 'Workflow dependency results'), guidedText('p', 'workspace-eyebrow', `${campaign.workflowName} · ${campaign.environmentId}`));
        sourceBadge('Recorded experiment');
        const loading = guidedText('p', 'workspace-status', 'Loading recorded evidence…');
        guidedResult.append(loading);
        try {
            let assessment, approved = false;
            try { assessment = await requestScenario(`${campaignUrl(campaign)}/assessment`); }
            catch (error) { if (error.status !== 404) throw error; }
            // Recorded results stay read-only views: the step comes from what is stored, not from progress checks.
            if (assessment) {
                try { await requestScenario(`${campaignUrl(campaign)}/approval`); approved = true; }
                catch (error) { if (error.status !== 404) throw error; }
            }
            if (version !== renderVersion) return;
            loading.remove();
            if (assessment) {
                if (assessment.workflowId !== campaign.workflowId || assessment.environmentId !== campaign.environmentId) throw new Error('The assessment does not belong to this workflow environment.');
                guidedResult.replaceChildren(resultsDashboard(campaign, assessment, approved));
                return;
            } else {
                const graph = await requestScenario(`${campaignUrl(campaign)}/observed-graph`);
                if (version !== renderVersion) return;
                guidedResult.append(guidedText('p', 'workspace-status', 'This dataset contains observed traces but no saved dependency assessment. It cannot yet establish mandatory, supportive or optional dependencies.'), renderDependencyMap({...graph, entryService: campaign.entryService}));
            }
            if (version === renderVersion) guidedResult.append(details('Execution definition', guidedText('p', '', executionSummary(campaign)), guidedText('pre', '', JSON.stringify(campaign.executionDefinition, null, 2))), button('Collect additional evidence', () => renderCampaign(campaign), true));
        } catch (error) {
            if (version === renderVersion) { loading.remove(); guidedResult.append(guidedText('p', 'is-error', `Recorded evidence unavailable: ${error.message}`), button('Retry', () => renderRecordedCampaign(campaign), true)); }
        }
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
        stopDiscoveryScan();
        $('trace-start-prompt').hidden = true;
        $('trace-stop-button').hidden = true;
        $('find-workflows-status').textContent = '';
        traceUnavailable = false;
        traceControlsChecked = false;
        guidedConnectionVersion += 1;
        guidedWorkflows = [];
        guidedService.replaceChildren(new Option('Connect to load services', ''));
        guidedService.disabled = true;
        guidedServiceField.hidden = true;
        guidedDiscovery.hidden = true;
        $('discovery-coverage').hidden = true;
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
    const traceService = () => window.__resilienceTraceService || {available: false};
    function usesLocalJaeger() {
        const source = guidedConnectionPayload().traceSource;
        if (source.type !== 'jaeger') return false;
        try {
            const url = new URL(source.endpoint);
            // The extension helper manages this fixed collector endpoint, not arbitrary local servers.
            return ['localhost', '127.0.0.1', '[::1]', 'host.docker.internal'].includes(url.hostname)
                && url.protocol === 'http:' && url.port === '16686' && url.pathname === '/';
        }
        catch { return false; }
    }
    async function updateTraceControls(report, version) {
        if (version !== guidedConnectionVersion || traceActionBusy) return;
        const down = report.state === 'jaeger_unavailable';
        if (!down) {
            $('trace-start-prompt').hidden = true;
            traceUnavailable = false;
            message($('find-workflows-status'), '');
            if (!traceControlsChecked && usesLocalJaeger() && traceService().available) {
                traceControlsChecked = true;
                try {
                    const known = await traceService().status();
                    if (version === guidedConnectionVersion) $('trace-stop-button').hidden = known.state !== 'trace-running';
                } catch { /* Discovery remains usable when the optional host helper is unavailable. */ }
            }
            return;
        }
        if (!usesLocalJaeger()) {
            $('trace-start-prompt').hidden = true;
            message($('find-workflows-status'), 'The selected trace service is unavailable. Check its address and connection.');
            return;
        }
        if (!traceService().available) {
            $('trace-start-prompt').hidden = true;
            message($('find-workflows-status'), 'The local trace service is unavailable. Start it from your tracing setup, or use the Docker Desktop extension to start it here. Discovery will retry.');
            return;
        }
        // A declined start stays declined until the operator asks to find workflows again.
        if (traceUnavailable) return;
        traceUnavailable = true;
        try {
            const known = await traceService().status();
            if (version !== guidedConnectionVersion) return;
            $('trace-stop-button').hidden = known.state !== 'trace-running';
            if (['trace-not-created', 'trace-stopped'].includes(known.state)) {
                $('trace-start-prompt').hidden = false;
                message($('find-workflows-status'), 'The local trace service is stopped. Start it to collect traces.');
            } else message($('find-workflows-status'), known.message, !known.ok);
        } catch (error) {
            if (version === guidedConnectionVersion) message($('find-workflows-status'), 'The trace service helper could not be reached. Check Docker Desktop and try again.', true);
        }
    }
    async function runTraceServiceAction(action) {
        if (traceActionBusy || !traceService().available || !usesLocalJaeger()) return;
        if (action === 'start' && $('trace-start-prompt').hidden) return;
        if (action === 'stop' && $('trace-stop-button').hidden) return;
        const version = guidedConnectionVersion;
        traceActionBusy = true;
        const controls = ['guided-auto-discover', 'trace-start-confirm', 'trace-start-cancel', 'trace-stop-button'].map($);
        controls.forEach(item => item.disabled = true);
        $('trace-start-prompt').hidden = true;
        message($('find-workflows-status'), action === 'start' ? 'Starting the trace service… Docker may need to download its image.' : 'Stopping the trace service…');
        try {
            const result = await traceService()[action]();
            if (version !== guidedConnectionVersion) return;
            $('trace-stop-button').hidden = result.state !== 'trace-running';
            message($('find-workflows-status'), result.message, !result.ok);
            traceUnavailable = false;
            if (result.ok) discoveryPoller?.refresh();
        } catch (error) {
            if (version === guidedConnectionVersion) message($('find-workflows-status'), 'Docker Desktop did not complete the request. Try again.', true);
        } finally {
            traceActionBusy = false;
            controls.forEach(item => item.disabled = false);
        }
    }
    function findWorkflowsAutomatically() {
        if (traceActionBusy || !guidedForm.reportValidity()) return;
        traceUnavailable = false;
        traceControlsChecked = false;
        if (discoveryPoller?.isRunning()) { discoveryPoller.refresh(); return; }
        resetGuidedDiscovery();
        const version = guidedConnectionVersion;
        const {traceSource, lookback} = guidedConnectionPayload();
        const payload = {traceSource, lookback, continuous: true};
        let scanId = null, cancelled = false, batchInFlight = false;
        const cancelOnBackend = id => postCampaignAction('/api/workflows/discovery', {...payload, scanId: id, cancel: true}).catch(() => {});
        cancelActiveScan = () => {
            cancelled = true;
            if (scanId && !batchInFlight) cancelOnBackend(scanId);
        };
        const render = report => {
            if (version !== guidedConnectionVersion) return;
            const previous = selectedGuidedWorkflow(guidedWorkflows, guidedOperation.value);
            guidedWorkflows = report.workflows;
            guidedOperation.replaceChildren(new Option(report.workflows.length ? 'Choose an operation' : report.message, ''));
            report.workflows.forEach((item, index) => {
                guidedOperation.append(new Option(`${item.operationName} · ${item.rootService} · ${item.traceCount} traces`, String(index)));
                if (previous?.rootService === item.rootService && previous?.operationName === item.operationName) guidedOperation.value = String(index);
            });
            guidedDiscovery.hidden = report.workflows.length === 0;
            guidedInspectButton.disabled = !selectedGuidedWorkflow(guidedWorkflows, guidedOperation.value);
            const progress = report.coverage;
            const coverage = progress
                ? ` ${progress.status === 'complete' ? 'Search completed for the fixed window' : progress.status === 'scanning' ? 'Searching' : 'Partial search'} · ${progress.servicesCompleted}/${progress.servicesTotal} services completed · ${report.tracesFetched} unique traces · ${progress.queriesCompleted} batches.${progress.status === 'partial' ? ' Some windows could not be exhausted; other workflows may exist.' : ''}`
                : report.truncated ? ' Partial search; other workflows may exist.' : '';
            $('stop-workflow-scan').hidden = progress?.status !== 'scanning';
            $('discovery-coverage').hidden = !progress;
            $('discovery-coverage-details').textContent = progress ? JSON.stringify(progress, null, 2) : '';
            setGuidedStatus(`${report.message}${coverage}`);
            const help = $('guided-operation-help');
            if (help) help.textContent = 'Entry services and recorded operations were found by the backend. Choose the action you want to test; observed calls are not yet validated dependencies.';
        };
        discoveryPoller = window.WorkflowDiscoveryPoller.create({
            // The discovery API handles availability for every configured trace backend.
            checkHealth: async () => true,
            discover: async () => {
                batchInFlight = true;
                $('stop-workflow-scan').hidden = false;
                try {
                    const report = await postCampaignAction('/api/workflows/discovery', {...payload, ...(scanId ? {scanId} : {})});
                    if (cancelled || version !== guidedConnectionVersion) {
                        if (report.scanId && report.coverage?.status === 'scanning') await cancelOnBackend(report.scanId);
                        return report;
                    }
                    scanId = report.coverage?.status === 'scanning' ? report.scanId : null;
                    return report;
                } finally { batchInFlight = false; }
            },
            onChecking: () => setGuidedStatus('Finding recorded workflows across services…'),
            onWaiting: () => setGuidedStatus('Waiting for the trace service…'),
            onReport: report => {
                render(report); updateTraceControls(report, version);
                if (report.coverage?.status === 'partial' && report.state !== 'jaeger_unavailable') discoveryPoller.stop();
            },
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
        stopDiscoveryScan();
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
        stopDiscoveryScan();
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
        return {observedServices: [...session.observedServices], entryService: session.entryService,
            edges: session.edges.map(({source, target}) => ({source, target})), mandatoryServices: [], optionalServices: []};
    }
    function renderDependencyMap(graph, assessment = null, sourceLabel = 'Observed trace evidence · classifications pending') {
        const host = document.createElement('div');
        if (!window.WorkflowDependencyMap) {
            host.append(guidedText('p', 'is-error', 'The dependency diagram could not load. Recorded calls and evidence remain available below.'));
            return host;
        }
        window.WorkflowDependencyMap.render(host, {
            observedServices: graph.observedServices || [], edges: graph.edges || [], entryService: graph.entryService,
            assessments: assessment?.services || [], sourceLabel: assessment && sourceLabel === 'Observed trace evidence · classifications pending' ? 'Saved dependency assessment' : sourceLabel,
        });
        return host;
    }
    function renderObservedGraph(session) {
        const canvas = document.createElement('div');
        renderGraph(canvas, observedGraphModel(session));
        const calls = document.createElement('ul');
        session.edges.forEach(edge => {
            const call = guidedText('li', '', `${edge.source} → ${edge.target}`);
            if (edge.evidence?.length) call.append(details('Trace evidence', ...edge.evidence.map(item => {
                const record = guidedText('div', '', `${item.stepName || 'Selected request'} · trace ${item.traceId} · ${item.quality} · ${item.sha256}`);
                (item.spans || []).forEach(span => record.append(guidedText('p', '', `${span.basis}: ${span.callerSpanId} (${span.callerOperation})${span.calleeSpanId ? ` → ${span.calleeSpanId} (${span.calleeOperation})` : ' · target identified by client peer attribute'}`)));
                return record;
            })));
            calls.append(call);
        });
        const panel = details(`Recorded calls · ${session.observedServices.length} services · ${session.edges.length} connections`,
            guidedText('p', 'workspace-help', 'Not tested yet: labels appear after the outage tests.'), canvas, details('Connections and trace evidence', calls));
        panel.open = true;
        return panel;
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
        stopDiscoveryScan();
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
        guidedResult.append(metrics, renderObservedGraph(session), serviceFrequencyTable(session));
        guidedResult.append(details('Evidence used for this graph',
            guidedText('p', '', 'Arrows show observed calls, not proven necessity. Service labels require controlled tests against your success checks. Missing telemetry can hide calls.'),
            guidedText('pre', '', JSON.stringify({source: session.traceSource || session.jaegerUrl, lookback: session.lookback, traceQuality: session.traceQuality, serviceEvidence: session.serviceEvidence, edges: session.edges}, null, 2))));
        if (session.evidenceTier === 'production') {
            guidedResult.append(guidedText('p', 'workspace-status', 'Production connection: observation only. Run dependency experiments in a separate test environment; traces alone cannot establish mandatory, optional or supporting dependencies.'));
            return;
        }
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
        // Suggestions come only from the recorded root requests of this operation, shown with their counts.
        const observed = requirements.observedRequests || {};
        if (observed.suggestedSuccessStatusCodes?.length) status.input.value = observed.suggestedSuccessStatusCodes.join(', ');
        const suggestedAddress = observed.suggestedApplicationUrl;
        const appAddress = field('Application base URL', 'url', suggestedAddress?.url || '', suggestedAddress
            ? `Read from the traces: ${suggestedAddress.basis} (${suggestedAddress.recordedUrl}).${suggestedAddress.note ? ` ${suggestedAddress.note}` : ''}`
            : 'The traces do not record the address the application was reached at. Enter its base address.');
        appAddress.input.required = true;
        appAddress.label.classList.add('full-width');
        actionSection.append(observedRequestEvidence(observed));
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
        form.append(title, name.label, outcome.label, appAddress.label, preparationSection, actionSection, criticality.label, trials.label, advanced, check, preview, confirm.label, submit, notice);
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
                remember(`workflow-application:${campaign.campaignId}`, appAddress.input.value.trim());
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
    function serviceFrequencyTable(session) {
        const table = guidedText('table', 'workspace-table', '');
        const head = document.createElement('tr');
        ['Service', 'Traces containing it', 'Frequency'].forEach(text => head.append(guidedText('th', '', text)));
        table.append(head);
        (session.serviceEvidence || []).forEach(item => {
            const row = document.createElement('tr');
            [item.service, `${item.traceCount} of ${session.traceCount}`, `${item.traceCount} / ${session.traceCount} = ${Number(item.frequency).toFixed(3)}`].forEach(text => row.append(guidedText('td', '', text)));
            table.append(row);
        });
        const wrap = guidedText('div', 'workspace-table-scroll', '');
        wrap.append(table);
        return details('How often each service appears in the matching traces', guidedText('p', 'workspace-help', 'Frequency = traces in which the service has a span / matching traces. This is observation only: it shows participation, not whether the workflow needs the service.'), wrap);
    }
    function observedRequestEvidence(observed) {
        if (!observed.requestCount) return guidedText('p', 'workspace-help', 'No root-request evidence was saved with this inspection. Inspect the operation again to read status codes and the application address from its traces.');
        const table = guidedText('table', 'workspace-table', '');
        const head = document.createElement('tr');
        ['HTTP status', 'Recorded requests', 'With an error span status', 'Suggested as success'].forEach(text => head.append(guidedText('th', '', text)));
        table.append(head);
        observed.statusCodes.forEach(item => {
            const row = document.createElement('tr');
            [item.code, String(item.count), String(item.withSpanError), observed.suggestedSuccessStatusCodes.includes(Number(item.code)) ? 'Yes' : 'No'].forEach(text => row.append(guidedText('td', '', text)));
            table.append(row);
        });
        const wrap = guidedText('div', 'workspace-table-scroll', '');
        wrap.append(table);
        const duration = observed.durationMs ? `Duration of the recorded requests: min ${observed.durationMs.min} ms · median ${observed.durationMs.median} ms · max ${observed.durationMs.max} ms.` : 'Durations were not recorded.';
        const panel = details(`What the ${observed.requestCount} recorded requests show`, wrap, guidedText('p', 'workspace-help', observed.successRule), guidedText('p', 'workspace-help', duration),
            guidedText('p', 'workspace-help', `Addresses recorded: ${observed.observedAddresses.map(item => `${item.url} (${item.count})`).join(', ') || 'none'}.`),
            guidedText('p', 'workspace-help', `Not recorded in traces, so you provide them: ${observed.notRecorded.join('; ')}.`));
        panel.open = true;
        return panel;
    }
    const stageLabels = {baseline_required: 'Normal runs needed', interventions_required: 'Service tests needed', ready_for_assessment: 'Ready to review', reviewed: 'Awaiting approval', approved: 'Approved'};
    function stageLabel(stage) { return stageLabels[stage] || 'Checking status'; }
    function executionSummary(campaign) {
        const definition = campaign.executionDefinition;
        if (!definition) return 'Execution definition unavailable.';
        return 'Actual requests: ' + definition.steps.map(step => `${step.method} ${step.path}${step.responseContains?.length ? ` · required response text: ${step.responseContains.map(value => JSON.stringify(value)).join(', ')}` : ''}`).join(' → ');
    }
    async function renderCampaign(campaign) {
        stopDiscoveryScan();
        guidedConnectionVersion += 1;
        offlineVersion += 1;
        const version = ++renderVersion;
        activeCampaign = campaign;
        setSelection(campaign.campaignId);
        $('source-panel').hidden = true;
        offlineResult.hidden = true;
        guidedResult.hidden = false;
        guidedResult.replaceChildren(guidedText('p', 'workspace-eyebrow', campaign.environmentId), guidedText('h3', '', campaign.workflowName));
        guidedResult.append(guidedText('p', 'workspace-help', executionSummary(campaign)));
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
            guidedResult.append(progressStepper(progress.stage), heading, pipelinePanel(campaign));
            const metrics = guidedText('div', 'workspace-metrics', '');
            metrics.append(resultCard('Normal runs', `${progress.baseline.eligibleTrials}/${progress.baseline.requiredTrials}`, `${progress.baseline.successfulTrials} successful · ${progress.baseline.failedTrials} failed · ${progress.baseline.excludedTrials} excluded`), resultCard(['approved', 'reviewed', 'ready_for_assessment'].includes(progress.stage) ? 'Services tested' : 'Services to test', String(campaign.candidateServices.length)));
            guidedResult.append(metrics, guidedText('p', '', `Success: ${campaign.successContract.outcome}`));
            const observed = guidedText('div', '', 'Loading recorded-call graph…');
            guidedResult.append(observed);
            requestScenario(`${campaignUrl(campaign)}/observed-graph`).then(graph => {
                if (version !== renderVersion) return;
                const panel = renderObservedGraph({...graph, entryService: campaign.entryService});
                panel.open = true;
                panel.append(guidedText('p', '', `${graph.completeSequenceRuns}/${graph.recordedRuns} runs have complete successful action traces. ${graph.legacySelectedTraceRuns} historical runs retain only the selected request.`));
                if (!graph.observedServices.length) panel.append(guidedText('p', '', 'No execution trace evidence has been recorded for this workflow yet. A baseline run collects evidence; its success checks must also pass.'));
                if (graph.findings.length) panel.append(details('Evidence gaps', ...graph.findings.map(item => guidedText('p', '', `${item.executionId}: ${item.reason}`))));
                observed.replaceWith(panel);
            }).catch(error => { if (version === renderVersion) message(observed, `Recorded calls unavailable: ${error.message}`, true); });
            if (progress.baseline.failedTrials) guidedResult.append(guidedText('p', 'is-error', `${progress.baseline.failedTrials} baseline run(s) failed the configured success checks. Read the run reasons below before repeating the test. The actual requests and required response text are shown above.`));
            if (progress.baseline.recordedTrials) guidedResult.append(renderRunHistory(campaign.baseline.batchId, 'Normal run results', progress.baseline.failedTrials > 0));
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
        } catch (error) {
            if (version !== renderVersion) return;
            message(loading, error.message, true);
            if (!loading.isConnected) guidedResult.append(loading);
            guidedResult.append(button('Retry', () => renderCampaign(campaign), true));
        }
    }
    function renderRunHistory(batchId, title, open = false) {
        const status = guidedText('p', 'workspace-status', '');
        const panel = details(title, status);
        let loaded = false;
        const load = async () => {
            if (!panel.open || loaded) return;
            loaded = true;
            message(status, 'Loading run results…');
            try {
                const runs = await requestScenario(`/api/workflows/executions?batch_id=${encodeURIComponent(batchId)}`);
                if (!panel.isConnected) return;
                message(status, runs.length ? `${runs.length} recorded runs` : 'No runs have been recorded.');
                const list = document.createElement('ul');
                runs.forEach(run => {
                    const item = guidedText('li', '', `${new Date(run.recordedAt).toLocaleString()} · ${run.status} · trace ${run.correlationStatus}${run.reasons.length ? ': ' + run.reasons.join(' ') : ''}`);
                    if (run.stepEvidence) {
                        const actions = document.createElement('ul');
                        run.stepEvidence.forEach(step => {
                            const action = guidedText('li', '', `${step.name}: ${step.attempted ? `${step.succeeded ? 'assertions passed' : 'assertions failed'} · HTTP ${step.statusCode ?? 'no response'} · trace ${step.traceId} (${step.correlationStatus})` : 'not attempted'}`);
                            if (step.assertions?.length) action.append(details('Success checks', ...step.assertions.map(check => guidedText('p', '', `${check.name}: ${check.passed ? 'passed' : 'failed'} · expected ${JSON.stringify(check.expected)} · observed ${JSON.stringify(check.observed)}`))));
                            actions.append(action);
                        });
                        item.append(actions);
                    } else item.append(guidedText('p', '', 'Historical record: per-action traces were not retained.'));
                    list.append(item);
                });
                panel.append(list);
            } catch (error) { loaded = false; message(status, error.message, true); }
        };
        panel.addEventListener('toggle', load);
        if (open) { panel.open = true; load(); }
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
        const extensionHint = window.ddClient ? ' Inside Docker Desktop, a host-published application uses http://host.docker.internal:<port>; localhost points to the extension backend.' : '';
        const item = field('Application base URL', 'url', stored(`workflow-application:${campaign.campaignId}`) || '', 'Enter the address of the application being tested, including http:// or https://. Use its base address, not the tracing server or a page path.' + extensionHint);
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
    function renderKubernetesOutagePanel(campaign, applicationUrl) {
        const panel = guidedText('section', 'request-section full-width', '');
        const body = guidedText('div', '', '');
        panel.append(guidedText('h5', '', 'Automatic outage tests on the test cluster'), body);
        message(body, 'Checking whether the test cluster allows outage tests…');
        let wasRunning = false;
        const outageUrl = `${campaignUrl(campaign)}/kubernetes-outages`;
        const refresh = async () => {
            let status;
            try { status = await requestScenario(outageUrl); }
            catch (error) { if (panel.isConnected) message(body, error.message, true); return; }
            if (!panel.isConnected) return;
            const job = status.job;
            const running = job?.state === 'running';
            // A finished job changed the recorded evidence: redraw the whole workflow from the backend.
            if (wasRunning && !running) { if (activeCampaign?.campaignId === campaign.campaignId) renderCampaign(campaign); return; }
            wasRunning = running;
            body.replaceChildren();
            if (status.busy) {
                body.append(guidedText('p', 'workspace-status', status.reason));
                return;
            }
            if (!status.available) {
                body.append(guidedText('p', 'workspace-help', `Not available here: ${status.reason} Use the manual controls below instead.`));
                return;
            }
            if (running) {
                body.append(guidedText('p', 'workspace-status', `Testing ${job.currentService || job.services[0]} · ${job.completedServices.length}/${job.services.length} services done. The service is scaled to zero in ${status.namespace}, the workflow runs, then the service is restored and verified ready.`));
                setTimeout(refresh, 3000);
                return;
            }
            if (job?.state === 'failed') body.append(guidedText('p', 'is-error', `The last outage test stopped: ${job.error}`));
            if (status.interrupted.length) {
                const notice = guidedText('p', 'workspace-status', '');
                const recover = button('Restore services', async () => {
                    recover.disabled = true;
                    message(notice, 'Restoring and waiting until the services are ready…');
                    try { await postCampaignAction(`${outageUrl}/recover`, {}); if (activeCampaign?.campaignId === campaign.campaignId) renderCampaign(campaign); }
                    catch (error) { message(notice, error.message, true); recover.disabled = false; }
                });
                body.append(guidedText('p', 'is-error', `Still scaled to zero from an earlier test: ${status.interrupted.join(', ')}.`), recover, notice);
                return;
            }
            const consent = field(`I approve scaling each untested candidate service in ${status.namespace} to zero, one at a time, and restoring it afterwards.`, 'checkbox');
            consent.label.className = 'checkbox-label full-width';
            const notice = guidedText('p', 'workspace-status', '');
            const start = button('Run outage tests for all services', async () => {
                if (!consent.input.checked) { consent.input.setCustomValidity('Approve the outages first.'); consent.input.reportValidity(); return; }
                if (!applicationUrl.reportValidity()) return;
                start.disabled = true;
                message(notice, 'Starting…');
                try {
                    await requestScenario(outageUrl, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Actor': 'workflow-ui', 'X-Outage-Confirm': 'scale-test-services-to-zero-and-restore'}, body: JSON.stringify({applicationUrl: applicationUrl.value.trim()})});
                    remember(`workflow-application:${campaign.campaignId}`, applicationUrl.value.trim());
                    refresh();
                } catch (error) { message(notice, error.message, true); start.disabled = false; }
            });
            consent.input.addEventListener('change', () => consent.input.setCustomValidity(''));
            body.append(guidedText('p', 'workspace-help', 'The backend takes each service down, confirms through the Kubernetes API that no pod is left, runs the workflow trials, then restores the service and confirms it is ready. These runs count as backend-verified outages.'), consent.label, start, notice);
        };
        refresh();
        return panel;
    }
    async function loadInterventionConsole(campaign, target) {
        target.replaceChildren(guidedText('h4', '', 'Service availability tests'));
        const app = applicationField(campaign);
        target.append(app.label, renderKubernetesOutagePanel(campaign, app.input));
        try {
            // While an automatic test runs, its outages are intentional: no manual recording controls.
            const outages = await requestScenario(`${campaignUrl(campaign)}/kubernetes-outages`).catch(() => null);
            if (!target.isConnected || outages?.busy) return;
            target.append(guidedText('p', '', 'Manual alternative: record an outage you caused yourself, run the workflow, then record recovery. These controls record evidence; they do not stop or restart services.'));
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
    const labelNames = {mandatory: 'Mandatory', optional: 'Optional', supporting: 'Supporting', conditional: 'Conditional', insufficient_evidence: 'Unresolved'};
    const groupOf = label => ['mandatory', 'optional', 'supporting'].includes(label) ? label : 'unresolved';
    function badge(text, kind) { return guidedText('span', `wd-badge wd-badge-${kind}`, text); }
    const fixed = (number, digits = 3) => number == null ? '—' : Number(number).toFixed(digits);
    const range = interval => interval ? `${fixed(interval[0])} – ${fixed(interval[1])}` : '—';
    // A compact calculation sheet for one service: every number the label rests on, no prose.
    function calculationSheet(service, policy) {
        const b = service.baseline, o = service.intervention, s = service.statistics || {};
        const rate = cohort => `${cohort.succeededRecords} / ${cohort.eligibleRecords} = ${fixed(cohort.successRate)}`;
        const tick = ok => ok ? '✓' : '✗';
        const effect = service.failureEffect;
        let rule = '—';
        if (effect != null) {
            if (effect >= policy.mandatoryEffectThreshold) rule = `Δ ≥ ${policy.mandatoryEffectThreshold} and outage ≤ ${policy.maxMandatorySuccessRate} → ${o.successRate <= policy.maxMandatorySuccessRate ? 'Mandatory' : 'Unresolved'}`;
            else if (effect < -policy.optionalEffectTolerance) rule = `Δ < −${policy.optionalEffectTolerance} → Unresolved`;
            else if (effect <= policy.optionalEffectTolerance) rule = `|Δ| ≤ ${policy.optionalEffectTolerance} → Optional`;
            else rule = `${policy.optionalEffectTolerance} < Δ < ${policy.mandatoryEffectThreshold} → Supporting`;
        }
        const rows = [
            ['Normal success', rate(b), range(s.baselineRateInterval)],
            ['Outage success', rate(o), range(s.outageRateInterval)],
            ['Effect Δ', effect == null ? '—' : `${fixed(b.successRate)} − ${fixed(o.successRate)} = ${fixed(effect)}`, range(s.effectInterval)],
            ['Decision', rule, labelNames[service.classification] || service.classification],
            ['Gates', `runs ≥ ${policy.minSamplesPerCohort}: ${tick(b.eligibleRecords >= policy.minSamplesPerCohort)} ${tick(o.eligibleRecords >= policy.minSamplesPerCohort)} · normal ≥ ${policy.minBaselineSuccessRate}: ${tick((b.successRate ?? 0) >= policy.minBaselineSuccessRate)}`, `excluded ${b.excludedRecords + o.excludedRecords}`],
            ['Fisher p (one-sided)', s.fisherOneSidedP == null ? '—' : Number(s.fisherOneSidedP).toPrecision(3), ''],
            ['Evidence strength', fixed(service.evidenceStrength, 2), `(eligible ÷ recorded) × min(1, n ÷ ${policy.minSamplesPerCohort})`],
            ['Confidence', s.supportedAtConfidence == null ? '—' : s.supportedAtConfidence ? `Firm at ${Math.round(s.confidenceLevel * 100)}%` : `Not firm at ${Math.round(s.confidenceLevel * 100)}%`, s.runsNeededPerCohort ? `≈ ${s.runsNeededPerCohort} runs / group` : ''],
        ];
        const sheet = guidedText('dl', 'wd-sheet', '');
        rows.forEach(([label, value, aside]) => {
            const row = guidedText('div', 'wd-sheet-row', '');
            row.append(guidedText('dt', '', label), guidedText('dd', '', value), guidedText('dd', 'wd-sheet-aside', aside));
            sheet.append(row);
        });
        if (service.reasons?.length) sheet.append(guidedText('p', 'wd-sheet-note', service.reasons.join(' ')));
        return sheet;
    }
    function servicesTable(assessment, filter) {
        const table = guidedText('table', 'workspace-table wd-services', '');
        const head = document.createElement('tr');
        ['Service', 'Dependency', 'Normal', 'Outage', 'Effect Δ', '95% interval of Δ', 'p', 'Confidence'].forEach(text => head.append(guidedText('th', '', text)));
        const thead = document.createElement('thead');
        thead.append(head);
        const body = document.createElement('tbody');
        const order = {mandatory: 0, supporting: 1, optional: 2, unresolved: 3};
        [...assessment.services].sort((a, c) => order[groupOf(a.classification)] - order[groupOf(c.classification)] || a.serviceName.localeCompare(c.serviceName)).forEach(service => {
            const group = groupOf(service.classification);
            const s = service.statistics || {};
            const row = guidedText('tr', 'wd-service-row', '');
            row.dataset.group = group;
            row.tabIndex = 0;
            const name = guidedText('td', 'wd-service-name', '');
            name.append(guidedText('span', 'wd-caret', '▸'), guidedText('span', '', service.serviceName));
            const dependency = document.createElement('td');
            dependency.append(badge(labelNames[service.classification] || service.classification, group));
            const confidence = document.createElement('td');
            confidence.append(s.supportedAtConfidence == null ? badge('—', 'muted') : s.supportedAtConfidence ? badge('Firm', 'firm') : badge(`≈${s.runsNeededPerCohort ?? '?'} runs`, 'pending'));
            row.append(name, dependency,
                guidedText('td', 'wd-num', `${service.baseline.succeededRecords}/${service.baseline.eligibleRecords}`),
                guidedText('td', 'wd-num', service.intervention.eligibleRecords ? `${service.intervention.succeededRecords}/${service.intervention.eligibleRecords}` : '—'),
                guidedText('td', 'wd-num', fixed(service.failureEffect, 2)),
                guidedText('td', 'wd-num', range(s.effectInterval)),
                guidedText('td', 'wd-num', s.fisherOneSidedP == null ? '—' : Number(s.fisherOneSidedP).toPrecision(2)),
                confidence);
            const detail = guidedText('tr', 'wd-detail-row', '');
            detail.hidden = true;
            const cell = document.createElement('td');
            cell.colSpan = 8;
            cell.append(calculationSheet(service, assessment.policy));
            detail.append(cell);
            const toggle = () => { detail.hidden = !detail.hidden; row.classList.toggle('is-open', !detail.hidden); };
            row.addEventListener('click', toggle);
            row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); } });
            if (filter && filter !== group) { row.hidden = true; }
            body.append(row, detail);
        });
        table.append(thead, body);
        const wrap = guidedText('div', 'workspace-table-scroll', '');
        wrap.append(table);
        return wrap;
    }
    function tabs(panels) {
        const root = guidedText('div', 'wd-tabs', '');
        const bar = guidedText('div', 'wd-tab-bar', '');
        bar.setAttribute('role', 'tablist');
        const views = panels.map(([label, content], index) => {
            const tab = guidedText('button', 'wd-tab', label);
            tab.type = 'button';
            tab.setAttribute('role', 'tab');
            const panel = guidedText('div', 'wd-tab-panel', '');
            panel.setAttribute('role', 'tabpanel');
            panel.append(content);
            const select = () => views.forEach(view => {
                const active = view.tab === tab;
                view.tab.setAttribute('aria-selected', String(active));
                view.panel.hidden = !active;
                if (active && view.onShow) view.onShow();
            });
            tab.addEventListener('click', select);
            bar.append(tab);
            return {tab, panel, select, onShow: content.onShow};
        });
        root.append(bar, ...views.map(view => view.panel));
        views[0].select();
        return root;
    }
    function resultsDashboard(campaign, assessment, approved) {
        const root = guidedText('section', 'wd-dashboard', '');
        const services = assessment.services || [];
        const header = guidedText('div', 'wd-dash-header', '');
        const title = guidedText('div', '', '');
        const meta = guidedText('div', 'wd-meta', '');
        meta.append(badge(assessment.environmentId, 'muted'), badge(`Assessed ${new Date(assessment.createdAt).toLocaleDateString()}`, 'muted'),
            approved ? badge('Approved', 'firm') : badge('Not approved', 'pending'));
        title.append(guidedText('h3', '', campaign.workflowName), meta);
        const actions = guidedText('div', 'wd-actions', '');
        const repeat = repeatPanel(campaign);
        const approvalSlot = guidedText('span', 'wd-action-slot', '');
        const approval = approvalPanel(campaign, assessment, approved, meta, approvalSlot);
        actions.append(approvalSlot, button('Repeat experiment', () => { repeat.hidden = !repeat.hidden; approval.hidden = true; }, true),
            button('Collect more evidence', () => renderCampaign(campaign), true),
            button('Download', () => downloadJsonArtifact(`${campaign.workflowId}-recorded-assessment.json`, assessment), true));
        header.append(title, actions);
        root.append(header, approval, repeat);
        // Summary tiles; the four label tiles filter the services table.
        const kpis = guidedText('div', 'wd-kpis', '');
        let filter = null;
        const servicesHost = document.createElement('div');
        const redraw = () => servicesHost.replaceChildren(servicesTable(assessment, filter));
        [['mandatory', 'Mandatory'], ['optional', 'Optional'], ['supporting', 'Supporting'], ['unresolved', 'Unresolved']].forEach(([group, label]) => {
            const names = services.filter(item => groupOf(item.classification) === group).map(item => item.serviceName);
            const tile = guidedText('button', `wd-kpi wd-kpi-${group}`, '');
            tile.type = 'button';
            tile.title = names.join(', ') || 'none';
            tile.append(guidedText('span', 'wd-kpi-value', String(names.length)), guidedText('span', 'wd-kpi-label', label));
            tile.addEventListener('click', () => {
                filter = filter === group ? null : group;
                kpis.querySelectorAll('.wd-kpi').forEach(item => item.classList.toggle('is-active', item === tile && filter === group));
                redraw();
            });
            kpis.append(tile);
        });
        const decided = services.filter(item => item.statistics && item.statistics.supportedAtConfidence !== null);
        const confidence = guidedText('div', 'wd-kpi wd-kpi-static', '');
        confidence.append(guidedText('span', 'wd-kpi-value', `${decided.filter(item => item.statistics.supportedAtConfidence).length}/${decided.length}`),
            guidedText('span', 'wd-kpi-label', decided.length ? `Firm at ${Math.round(decided[0].statistics.confidenceLevel * 100)}%` : 'Firm'));
        const validation = guidedText('div', 'wd-kpi wd-kpi-static', '');
        const validationValue = guidedText('span', 'wd-kpi-value', '—');
        validation.append(validationValue, guidedText('span', 'wd-kpi-label', 'Validation F1 · labels / baseline'));
        const differing = (assessment.traceOnlyBaseline?.comparison || []).filter(row => !row.agrees).map(row => row.serviceName);
        const traceOnly = guidedText('div', 'wd-kpi wd-kpi-static', '');
        traceOnly.title = differing.length ? `The trace baseline calls these mandatory: ${differing.join(', ')}` : 'Same labels as the trace baseline';
        traceOnly.append(guidedText('span', 'wd-kpi-value', String(differing.length)), guidedText('span', 'wd-kpi-label', 'Differ from trace baseline'));
        kpis.append(confidence, traceOnly, validation);
        requestScenario(`${campaignUrl(campaign)}/held-out-runs`).then(runs => {
            const done = runs.filter(run => run.counts.decided > 0);
            if (!done.length) return;
            const run = done[done.length - 1];
            validationValue.textContent = `${fixed(run.methodUncertainCountedWrong.f1, 2)} / ${fixed(run.traceOnly.f1, 2)}`;
        }).catch(() => {});
        redraw();
        // One graph: observed calls, nodes coloured by label.
        const graphPanel = document.createElement('div');
        const legend = guidedText('div', 'wd-legend', '');
        [['entry', 'Entry'], ['mandatory', 'Mandatory'], ['supporting', 'Supporting'], ['optional', 'Optional'], ['unresolved', 'Unresolved']].forEach(([kind, label]) => legend.append(badge(label, kind)));
        const calls = document.createElement('div');
        renderGraph(calls, assessmentGraph(assessment));
        graphPanel.append(legend, calls);
        const raw = guidedText('pre', 'wd-raw', JSON.stringify({successContract: campaign.successContract, policy: assessment.policy, traceOnlyBaseline: assessment.traceOnlyBaseline, services: assessment.services}, null, 2));
        root.append(kpis, tabs([['Services', servicesHost], ['Graph', graphPanel], ['Validation', heldOutPanel(campaign, true)], ['Raw data', raw]]));
        return root;
    }
    // Approval by a named person, then the freeze that hands the graph to failure prediction (Component 3).
    // The backend refuses approval while any service is unresolved; the freeze re-derives the evidence record.
    function approvalPanel(campaign, assessment, approved, meta, slot) {
        const panel = guidedText('section', 'wd-run wd-repeat', '');
        panel.hidden = true;
        const freezeUrl = `/api/workflows/${encodeURIComponent(assessment.workflowId)}`;
        const unresolved = (assessment.services || []).filter(item => groupOf(item.classification) === 'unresolved').map(item => item.serviceName);
        const freeze = actor => postCampaignAction(`${freezeUrl}/freeze`, {assessmentId: assessment.assessmentId,
            freezeId: `freeze-${assessment.assessmentId.split(':').pop()}`}, actor)
            .catch(error => { if (error.status !== 409) throw error; });   // already frozen: nothing to do
        const name = field('Your name', 'text', '');
        const note = field('Review note', 'textarea', '');
        const consent = field('I reviewed these results and approve this graph for failure prediction', 'checkbox');
        consent.label.className = 'checkbox-label full-width';
        [name.input, note.input].forEach(input => { input.required = true; });
        const notice = guidedText('p', 'workspace-status', '');
        const submit = button(approved ? 'Freeze' : 'Approve & freeze', async () => {
            if (!consent.input.checked || !name.input.value.trim() || (!approved && !note.input.value.trim())) {
                message(notice, 'Enter your name and a note, and confirm the review.', true); return;
            }
            submit.disabled = true;
            try {
                if (!approved) {
                    message(notice, 'Recording the approval…');
                    await postCampaignAction(`${campaignUrl(campaign)}/approval`, {rationale: note.input.value.trim(), approvalConfirmed: true}, name.input.value.trim());
                }
                message(notice, 'Freezing the graph with its evidence record…');
                await freeze(name.input.value.trim());
                renderRecordedCampaign(campaign);
            } catch (error) { message(notice, error.message, true); submit.disabled = false; }
        });
        const fields = guidedText('div', 'workspace-form wd-toolbar', '');
        fields.append(name.label);
        if (!approved) fields.append(note.label);
        panel.append(guidedText('h5', '', approved ? 'Freeze for failure prediction' : 'Approve and freeze'),
            guidedText('p', 'wd-run-meta', 'The frozen graph, with the traces of every counted run, is what failure prediction reads.'),
            fields, consent.label, submit, notice);
        if (unresolved.length && !approved) {
            submit.disabled = true;
            message(notice, `Unresolved services cannot be approved: ${unresolved.join(', ')}. Repeat the experiment first.`, true);
        }
        // Header state: frozen chip, or the button that opens this panel.
        requestScenario(`${freezeUrl}/frozen`).then(frozen => {
            if (frozen?.assessmentId === assessment.assessmentId) meta.append(badge('Frozen · used by failure prediction', 'firm'));
            else throw new Error('older graph frozen');
        }).catch(() => {
            const open = button(approved ? 'Freeze for failure prediction' : 'Approve & freeze', () => { panel.hidden = !panel.hidden; });
            slot.append(open);
        });
        return panel;
    }
    // A new experiment from this one's reviewed setup, run end to end in the background.
    function repeatPanel(campaign) {
        const panel = guidedText('section', 'wd-run wd-repeat', '');
        panel.hidden = true;
        const runs = field('Runs per group', 'number', '10');
        Object.assign(runs.input, {min: '3', max: '30', step: '1'});
        runs.input.required = true;
        const app = applicationField(campaign);
        if (!app.input.value) {
            // The address the backend used for this workflow; read from the latest inspection when nothing is stored.
            requestScenario('/api/workflows/analysis-sessions').then(sessions => {
                const latest = [...sessions].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
                    .find(item => item.rootRequestEvidence?.suggestedApplicationUrl);
                if (latest && !app.input.value) app.input.value = latest.rootRequestEvidence.suggestedApplicationUrl.url;
            }).catch(() => {});
        }
        const consent = field('Reuse this experiment’s reviewed request steps and success checks, and scale each service to zero in the test namespace and restore it', 'checkbox');
        consent.label.className = 'checkbox-label full-width';
        const notice = guidedText('p', 'workspace-status', '');
        const start = button('Start', async () => {
            if (!consent.input.checked || !runs.input.reportValidity() || !app.input.reportValidity()) { message(notice, 'Confirm, and enter the runs per group and the application address.', true); return; }
            start.disabled = true;
            message(notice, 'Inspecting the operation again and creating the new experiment…');
            try {
                const result = await requestScenario(`${campaignUrl(campaign)}/repeat`, {method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-Actor': 'workflow-ui', 'X-Outage-Confirm': 'scale-test-services-to-zero-and-restore'},
                    body: JSON.stringify({requiredBaselineTrials: Number(runs.input.value), applicationUrl: app.input.value.trim()})});
                remember(`workflow-application:${result.campaign.campaignId}`, app.input.value.trim());
                await loadSavedWorkflows();
                renderCampaign(result.campaign);
            } catch (error) { message(notice, error.message, true); start.disabled = false; }
        });
        const controls = guidedText('div', 'workspace-form wd-toolbar', '');
        controls.append(runs.label, app.label);
        panel.append(guidedText('h5', '', 'Repeat with every service'), guidedText('p', 'wd-run-meta', 'New experiment · fresh inspection · all services of every step · same request steps and success checks'), controls, consent.label, start, notice);
        return panel;
    }
    // Progress of an experiment running end to end; switches to its results when it finishes.
    function pipelinePanel(campaign) {
        const box = guidedText('div', '', '');
        let wasRunning = false;
        let seenStep = null;
        const refresh = async () => {
            let status;
            try { status = await requestScenario(`${campaignUrl(campaign)}/pipeline`); } catch { return; }
            const job = status.job;
            if (!job || !box.isConnected) return;
            // The cards above (normal runs, services) come from one progress read; redraw them when the job moves on.
            const step = `${job.phase}|${job.baselineRuns}|${job.servicesDone}`;
            if (job.state === 'running' && seenStep !== null && step !== seenStep && activeCampaign?.campaignId === campaign.campaignId) {
                renderCampaign(campaign);
                return;
            }
            seenStep = step;
            const phase = {starting: 'Starting', baseline: `Normal runs ${job.baselineRuns || ''}`,
                outages: `Outage tests · ${job.currentService || ''} (${job.servicesDone || ''} services done)`,
                assessment: 'Calculating results', done: 'Done'}[job.phase] || job.phase;
            if (job.state === 'running') {
                wasRunning = true;
                box.replaceChildren(guidedText('p', 'workspace-status', `Running end to end · ${phase}`));
                setTimeout(refresh, 4000);
            } else if (job.state === 'failed') {
                box.replaceChildren(guidedText('p', 'is-error', `Stopped during ${job.phase}: ${job.error}`));
            } else if (wasRunning && activeCampaign?.campaignId === campaign.campaignId) {
                renderRecordedCampaign(campaign);
            }
        };
        refresh();
        return box;
    }
    // Where an experiment stands, from the backend's own stage; the page never infers progress itself.
    const experimentSteps = [
        ['defined', 'Workflow defined'], ['baseline_required', 'Normal runs'], ['interventions_required', 'Outage tests'],
        ['ready_for_assessment', 'Results'], ['approved', 'Approved & frozen'], ['validated', 'Held-out validated'],
    ];
    function progressStepper(stage, validated = false) {
        const order = ['baseline_required', 'interventions_required', 'ready_for_assessment', 'reviewed', 'approved'];
        const reached = order.indexOf(stage);
        const list = guidedText('ol', 'experiment-steps-tracker', '');
        list.setAttribute('aria-label', 'Experiment progress');
        experimentSteps.forEach(([key, label], index) => {
            let state;
            if (key === 'defined') state = 'done';
            else if (key === 'validated') state = validated ? 'done' : (stage === 'approved' || stage === 'reviewed' ? 'current' : 'todo');
            else if (key === 'ready_for_assessment') state = reached > order.indexOf('ready_for_assessment') ? 'done' : stage === 'ready_for_assessment' ? 'current' : 'todo';
            else if (key === 'approved') state = stage === 'approved' ? 'done' : stage === 'reviewed' ? 'current' : 'todo';
            else state = reached > order.indexOf(key) ? 'done' : stage === key ? 'current' : 'todo';
            const item = guidedText('li', `step-${state}`, '');
            item.append(guidedText('span', 'step-number', state === 'done' ? '✓' : String(index + 1)), guidedText('span', 'step-label', label));
            if (state === 'current') item.setAttribute('aria-current', 'step');
            list.append(item);
        });
        return list;
    }
    // The answer first: label groups, how firm they are, and how they compare with traces alone.
    function resultsSummary(campaign, assessment) {
        const services = assessment.services || [];
        const box = guidedText('section', 'results-summary', '');
        const groups = guidedText('div', 'results-summary-groups', '');
        [['mandatory', 'Mandatory'], ['optional', 'Optional'], ['supporting', 'Supporting'], ['unresolved', 'Unresolved']].forEach(([key, label]) => {
            const names = services.filter(item => key === 'unresolved'
                ? !['mandatory', 'optional', 'supporting'].includes(item.classification) : item.classification === key).map(item => item.serviceName);
            const card = guidedText('div', `summary-group summary-${key}`, '');
            card.append(guidedText('span', 'summary-count', String(names.length)), guidedText('strong', '', label), guidedText('small', '', names.join(', ') || 'none'));
            groups.append(card);
        });
        box.append(guidedText('h4', '', 'Result'), groups);
        const decided = services.filter(item => item.statistics && item.statistics.supportedAtConfidence !== null);
        if (decided.length) {
            const firm = decided.filter(item => item.statistics.supportedAtConfidence).length;
            const level = Math.round(decided[0].statistics.confidenceLevel * 100);
            const needed = Math.max(0, ...decided.map(item => item.statistics.runsNeededPerCohort || 0));
            box.append(guidedText('p', 'summary-line', `Confidence: ${firm} of ${decided.length} labels hold at ${level}% confidence${firm < decided.length && needed ? `; about ${needed} usable runs per group would support all of them if the rates stay the same` : ''}.`));
        }
        const baseline = assessment.traceOnlyBaseline;
        if (baseline) {
            const differ = baseline.comparison.filter(row => !row.agrees).map(row => row.serviceName);
            box.append(guidedText('p', 'summary-line', `Trace baseline: treats all ${baseline.mandatoryServices.length} observed services as mandatory; the outage-tested labels differ on ${differ.length}${differ.length ? ` (${differ.join(', ')})` : ''}.`));
        }
        const heldOut = guidedText('p', 'summary-line', 'Held-out validation: loading…');
        box.append(heldOut);
        requestScenario(`${campaignUrl(campaign)}/held-out-runs`).then(runs => {
            const done = runs.filter(run => run.counts.decided > 0);
            if (!done.length) { heldOut.textContent = 'Held-out validation: not run yet. Use the Held-out validation section below.'; return; }
            const run = done[done.length - 1];
            const value = number => number == null ? 'n/a' : number.toFixed(2);
            heldOut.textContent = `Held-out validation (${run.counts.decided} scenarios, ${run.samplingMethod || 'uniform'} sample): outage-tested labels F1 ${value(run.method.f1)} on ${run.method.scored} predictions (strict ${value(run.methodUncertainCountedWrong.f1)}) · trace baseline F1 ${value(run.traceOnly.f1)}.`;
        }).catch(() => { heldOut.textContent = 'Held-out validation: unavailable.'; });
        return box;
    }
    function scoreRow(name, metrics, hint = '') {
        const value = number => number == null ? 'n/a' : number.toFixed(3);
        const interval = metrics.accuracyInterval ? ` [${value(metrics.accuracyInterval[0])}, ${value(metrics.accuracyInterval[1])}]` : '';
        const row = document.createElement('tr');
        row.title = hint;
        [name, String(metrics.scored), `${metrics.truePositives}/${metrics.falsePositives}/${metrics.falseNegatives}/${metrics.trueNegatives}`,
            value(metrics.precision), value(metrics.recall), value(metrics.f1), value(metrics.accuracy) + interval].forEach(text => row.append(guidedText('td', '', text)));
        return row;
    }
    function heldOutRun(campaign, run, refresh, openScenarios = new Set()) {
        const box = guidedText('section', 'wd-run', '');
        const counts = run.counts;
        const head = guidedText('div', 'wd-run-head', '');
        head.append(guidedText('h5', '', `Validation run · ${run.plannedAt ? new Date(run.plannedAt).toLocaleString() : run.runId}`),
            guidedText('span', `wd-badge wd-badge-${counts.observed === counts.planned && counts.planned ? 'firm' : 'pending'}`, `${counts.observed}/${counts.planned} scenarios`));
        head.title = `${run.runId} · seed ${run.seed ?? '—'} · predictions frozen before any scenario ran`;
        box.append(head);
        if (run.samplingGroups) box.append(guidedText('p', 'wd-run-meta', `${run.samplingMethod}: ${Object.entries(run.samplingGroups).map(([name, group]) => `${name}: ${group.drawn}/${group.available} pairs drawn`).join(' · ')}`));
        if (run.samplingNote) box.append(guidedText('p', 'wd-run-meta', run.samplingNote));
        const scores = guidedText('table', 'workspace-table', '');
        const header = document.createElement('tr');
        ['Predictor', 'Scored', 'TP/FP/FN/TN', 'Precision', 'Recall', 'F1', 'Accuracy (interval)'].forEach(text => header.append(guidedText('th', '', text)));
        scores.append(header,
            scoreRow('Outage-tested labels', run.method, 'Predicts failure when a mandatory service is down; pairs it cannot predict are left out'),
            scoreRow('Outage-tested labels (strict)', run.methodUncertainCountedWrong, 'Pairs the labels cannot predict are counted as wrong'),
            scoreRow('Trace baseline', run.traceOnly, 'Treats every service seen in traces as required, so it always predicts failure'));
        const scoreWrap = guidedText('div', 'workspace-table-scroll', '');
        scoreWrap.append(scores);
        box.append(scoreWrap, guidedText('p', 'wd-run-meta', `Failure counts as positive · no prediction ${counts.methodUncertain ?? 0} · degraded ${counts.degraded ?? 0} · unresolved ${counts.unresolved ?? 0}`));
        const table = guidedText('table', 'workspace-table', '');
        const columns = document.createElement('tr');
        ['#', 'Services down', 'Labels predict', 'Baseline predicts', 'Observed', 'Δ', 'Runs'].forEach(text => columns.append(guidedText('th', '', text)));
        table.append(columns);
        run.scenarios.forEach(row => {
            const line = document.createElement('tr');
            const seen = row.observation;
            line.title = Object.entries(row.prediction.labels).map(([name, label]) => `${name}: ${label}`).join(', ');
            [row.scenarioId, row.services.join(' + '), row.prediction.method === 'uncertain' ? 'no prediction' : row.prediction.method, row.prediction.traceOnly, seen ? seen.outcome : '—',
                seen && seen.effect != null ? seen.effect.toFixed(2) : '—',
                seen ? `${seen.scenario.succeededRecords}/${seen.scenario.eligibleRecords}` : '—'].forEach(text => line.append(guidedText('td', '', text)));
            table.append(line);
        });
        const wrap = guidedText('div', 'workspace-table-scroll', '');
        wrap.append(table);
        // A running job redraws this run every few seconds; the list stays open if the viewer opened it.
        const scenarioList = details(`Scenarios (${run.scenarios.length})`, wrap);
        scenarioList.open = openScenarios.has(run.runId);
        scenarioList.addEventListener('toggle', () => { if (scenarioList.open) openScenarios.add(run.runId); else openScenarios.delete(run.runId); });
        box.append(scenarioList);
        const job = run.job;
        if (job?.state === 'running') {
            box.append(guidedText('p', 'workspace-status', `Running ${job.currentScenario || ''} · ${job.completedScenarios.length}/${job.scenarios.length}`));
            setTimeout(refresh, 4000);
        } else if (counts.observed < counts.planned) {
            if (job?.state === 'failed') box.append(guidedText('p', 'is-error', `Stopped: ${job.error}`));
            const app = applicationField(campaign);
            const consent = field('Scale each pair to zero in the test namespace and restore it', 'checkbox');
            consent.label.className = 'checkbox-label full-width';
            const notice = guidedText('p', 'workspace-status', '');
            const start = button('Run scenarios', async () => {
                if (!consent.input.checked || !app.input.reportValidity()) { message(notice, 'Confirm the outages and enter the application address.', true); return; }
                start.disabled = true;
                try {
                    await requestScenario(`${campaignUrl(campaign)}/held-out-runs/${encodeURIComponent(run.runId)}/execute`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Actor': 'workflow-ui', 'X-Outage-Confirm': 'scale-test-services-to-zero-and-restore'}, body: JSON.stringify({applicationUrl: app.input.value.trim()})});
                    refresh();
                } catch (error) { message(notice, error.message, true); start.disabled = false; }
            });
            box.append(app.label, consent.label, start, notice);
        }
        return box;
    }
    // Validation runs: a toolbar to freeze a new run, then each run's scores. ``embedded`` loads at once (dashboard tab).
    function heldOutPanel(campaign, embedded = false) {
        const panel = embedded ? guidedText('div', 'wd-validation', '') : details('Validation');
        const body = guidedText('div', '', '');
        panel.append(body);
        // Built once: a running job redraws only the runs below, so typed inputs and opened lists survive each poll.
        const openScenarios = new Set();
        const count = field('Pairs to test', 'number', '10');
        Object.assign(count.input, {min: '1', max: '50', step: '1'});
        const sampling = selectField('Sampling method', [['stratified', 'Stratified'], ['uniform', 'Uniform']]);
        sampling.input.value = 'stratified';
        const seed = field('Random seed (optional)', 'number', '');
        const notice = guidedText('p', 'workspace-status', '');
        const plan = button('Freeze predictions for a new run', async () => {
            plan.disabled = true;
            try {
                await postCampaignAction(`${campaignUrl(campaign)}/held-out-runs`, {scenarioCount: Number(count.input.value), sampling: sampling.input.value, ...(seed.input.value ? {seed: Number(seed.input.value)} : {})});
                message(notice, '');
                refresh();
            } catch (error) { message(notice, error.message, true); }
            plan.disabled = false;
        }, true);
        const samplingControls = guidedText('div', 'workspace-form wd-toolbar', '');
        samplingControls.append(count.label, sampling.label, seed.label, plan);
        const runsView = guidedText('div', '', '');
        let shown = false;
        const refresh = async () => {
            let runs;
            try { runs = await requestScenario(`${campaignUrl(campaign)}/held-out-runs`); }
            catch (error) { if (panel.isConnected) message(shown ? notice : body, error.message, true); return; }
            if (!panel.isConnected) return;
            if (!shown) { body.replaceChildren(samplingControls, notice, runsView); shown = true; }
            runsView.replaceChildren(...[...runs].reverse().map(run => heldOutRun(campaign, run, refresh, openScenarios)));
            if (!runs.length) runsView.append(guidedText('p', 'wd-empty', 'No validation runs yet.'));
        };
        if (embedded) refresh();
        else panel.addEventListener('toggle', () => { if (panel.open && !panel.dataset.loaded) { panel.dataset.loaded = '1'; refresh(); } });
        return panel;
    }
    async function loadCampaignDecision(campaign, target, verification) {
        target.after(heldOutPanel(campaign));
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
        ['Service', 'Dependency', 'Normal successes', 'Outage successes', 'Success-rate drop', 'Excluded runs'].forEach(text => heading.append(guidedText('th', '', text)));
        header.append(heading);
        const body = document.createElement('tbody');
        assessment.services.forEach(service => {
            const row = document.createElement('tr');
            const count = cohort => `${cohort.succeededRecords}/${cohort.eligibleRecords}`;
            [service.serviceName, service.classification.replaceAll('_', ' '), count(service.baseline), count(service.intervention), service.failureEffect == null ? 'Unknown' : `${(service.failureEffect * 100).toFixed(1)} percentage points`, String(service.baseline.excludedRecords + service.intervention.excludedRecords)].forEach(text => row.append(guidedText('td', '', text)));
            body.append(row);
        });
        table.append(header, body);
        const wrap = guidedText('div', 'workspace-table-scroll', '');
        wrap.append(table);
        return wrap;
    }
    // The arithmetic behind each label, from the stored counts and the recorded policy; nothing is recomputed differently here.
    function calculationSteps(service, policy) {
        const rate = cohort => cohort.successRate == null ? 'undefined (no eligible runs)' : cohort.successRate.toFixed(3);
        const b = service.baseline, o = service.intervention;
        const steps = [
            `Normal runs: ${b.succeededRecords} successful / ${b.eligibleRecords} eligible = ${rate(b)} (${b.totalRecords} recorded, ${b.excludedRecords} excluded).`,
            `Runs with ${service.serviceName} unavailable: ${o.succeededRecords} successful / ${o.eligibleRecords} eligible = ${rate(o)} (${o.totalRecords} recorded, ${o.excludedRecords} excluded).`,
        ];
        if (service.failureEffect != null) steps.push(`Effect = ${rate(b)} − ${rate(o)} = ${service.failureEffect.toFixed(3)}.`);
        const gate = (label, value, test, threshold) => {
            const passed = test === '≥' ? Number(value) >= threshold : Number(value) <= threshold;
            return `${label}: ${value} ${test} ${threshold} → ${passed ? 'passed' : 'failed'}`;
        };
        steps.push(gate('Normal eligible runs', b.eligibleRecords, '≥', policy.minSamplesPerCohort),
            gate('Outage eligible runs', o.eligibleRecords, '≥', policy.minSamplesPerCohort));
        if (b.successRate != null) steps.push(gate('Normal success rate', b.successRate.toFixed(3), '≥', policy.minBaselineSuccessRate));
        const effect = service.failureEffect;
        if (effect != null) {
            let rule;
            if (effect >= policy.mandatoryEffectThreshold) rule = `effect ${effect.toFixed(3)} ≥ ${policy.mandatoryEffectThreshold} and outage success rate ${rate(o)} ${o.successRate <= policy.maxMandatorySuccessRate ? '≤' : '>'} ${policy.maxMandatorySuccessRate} → ${o.successRate <= policy.maxMandatorySuccessRate ? 'mandatory' : 'unresolved (runs still succeeded)'}`;
            else if (effect < -policy.optionalEffectTolerance) rule = `effect ${effect.toFixed(3)} < −${policy.optionalEffectTolerance} (success improved) → unresolved`;
            else if (effect <= policy.optionalEffectTolerance) rule = `|effect| ${Math.abs(effect).toFixed(3)} ≤ ${policy.optionalEffectTolerance} → optional`;
            else rule = `${policy.optionalEffectTolerance} < effect ${effect.toFixed(3)} < ${policy.mandatoryEffectThreshold} → supporting`;
            steps.push(`Decision rule: ${rule}.`);
        }
        const total = b.totalRecords + o.totalRecords, eligible = b.eligibleRecords + o.eligibleRecords;
        steps.push(`Evidence strength = (eligible ${eligible} / recorded ${total}) × min(1, min(${b.eligibleRecords}, ${o.eligibleRecords}) / ${policy.minSamplesPerCohort}) = ${service.evidenceStrength.toFixed(3)}.`);
        steps.push(`Result: ${service.classification.replaceAll('_', ' ')}. ${service.reasons.join(' ')}`);
        const stats = service.statistics;
        if (stats) {
            const range = interval => interval ? `[${interval[0].toFixed(3)}, ${interval[1].toFixed(3)}]` : 'undefined';
            const level = `${Math.round(stats.confidenceLevel * 100)}%`;
            steps.push(`${level} intervals (${stats.methods}): normal rate ${range(stats.baselineRateInterval)}, outage rate ${range(stats.outageRateInterval)}, effect ${range(stats.effectInterval)}.`);
            if (stats.fisherOneSidedP != null) steps.push(`One-sided Fisher exact test (outage lowered success): p = ${stats.fisherOneSidedP.toPrecision(3)}.`);
            stats.checks.forEach(check => steps.push(`Interval check: ${check}.`));
            steps.push(`Confidence: ${stats.verdict}.`);
        }
        return steps;
    }
    function traceOnlyComparison(assessment) {
        const baseline = assessment.traceOnlyBaseline;
        if (!baseline) return guidedText('p', 'workspace-help', 'This assessment has no trace baseline comparison.');
        const table = guidedText('table', 'workspace-table', '');
        const head = document.createElement('tr');
        ['Service', 'Trace baseline', 'Outage-tested label', 'Same answer'].forEach(text => head.append(guidedText('th', '', text)));
        table.append(head);
        baseline.comparison.forEach(row => {
            const line = document.createElement('tr');
            [row.serviceName, row.traceOnly, row.method.replaceAll('_', ' '), row.agrees ? 'Yes' : 'No'].forEach(text => line.append(guidedText('td', '', text)));
            table.append(line);
        });
        const wrap = guidedText('div', 'workspace-table-scroll', '');
        wrap.append(table);
        const differ = baseline.comparison.filter(row => !row.agrees).length;
        return details(`Trace baseline: ${baseline.mandatoryServices.length} mandatory · outage-tested labels differ on ${differ}`, guidedText('p', 'workspace-help', baseline.rule), wrap);
    }
    function calculationDetails(assessment) {
        return details('Calculation for each service', ...assessment.services.map(service => {
            const block = details(`${service.serviceName} · ${service.classification.replaceAll('_', ' ')}`);
            const list = guidedText('ol', '', '');
            calculationSteps(service, assessment.policy).forEach(step => list.append(guidedText('li', '', step)));
            block.append(list);
            return block;
        }));
    }
    function classificationKey() { return guidedText('p', 'workspace-help', 'Labels apply to these workflow inputs and success checks under the recorded assessment policy. Mandatory: the outage met the configured failure-effect thresholds. Optional / Supporting: no material success reduction, or an intermediate reduction, respectively. Supporting does not establish a loss of secondary functionality unless that outcome was measured. Unresolved: insufficient or condition-specific evidence. Node labels describe services; arrows require separate edge evidence.'); }
    function assessmentGraph(assessment) {
        // Project measured labels only. candidateGraph contains structural candidates,
        // whose mandatory slots must never be mistaken for assessed necessity.
        const services = assessment.services || [];
        const names = label => services.filter(item => item.classification === label).map(item => item.serviceName);
        return {
            observedServices: services.map(item => item.serviceName),
            entryService: assessment.structuralEntryService,
            edges: assessment.candidateGraph.edges,
            mandatoryServices: names('mandatory'),
            optionalServices: names('optional'),
            supportingServices: names('supporting'),
            unresolvedServices: services.filter(item => !['mandatory', 'optional', 'supporting'].includes(item.classification)).map(item => item.serviceName),
            edgeAssessments: assessment.edges || [],
        };
    }
    function dependencyGroups(graph) {
        const groups = guidedText('div', 'workspace-metrics', '');
        const optional = (graph.optionalServices || []).map(name => `${name} (optional)`);
        const supporting = (graph.supportingServices || []).map(name => `${name} (supporting)`);
        groups.append(resultCard('Mandatory', (graph.mandatoryServices || []).join(', ') || 'None established'),
            resultCard('Optional / Supporting', [...optional, ...supporting].join(', ') || 'None established'),
            resultCard('Unresolved', (graph.unresolvedServices || []).join(', ') || 'None'));
        return groups;
    }
    function renderCampaignAssessment(campaign, assessment, target) {
        const graph = assessmentGraph(assessment);
        const canvas = document.createElement('div');
        renderGraph(canvas, graph);
        target.replaceChildren(guidedText('h4', '', 'Review dependencies'), resultsSummary(campaign, assessment), renderDependencyMap(graph, assessment), classificationKey(),
            guidedText('h4', 'section-title', 'Evidence and calculations'), details('Run outcomes and the calculation for each service', assessmentRows(assessment), calculationDetails(assessment)),
            traceOnlyComparison(assessment), details('Service-to-service call graph', canvas));
        target.append(details('How these results are calculated',
            guidedText('p', '', 'Success rate = successful eligible runs / eligible runs. Effect = baseline success rate − intervention success rate. Excluded or unmatched runs do not count as successes or failures. Evidence strength measures usable evidence and minimum sample coverage; it is not a statistical confidence percentage.'),
            guidedText('p', '', `Recorded policy: at least ${assessment.policy.minSamplesPerCohort} eligible runs per cohort and baseline success rate ≥ ${assessment.policy.minBaselineSuccessRate}. Mandatory requires effect ≥ ${assessment.policy.mandatoryEffectThreshold} and outage success rate ≤ ${assessment.policy.maxMandatorySuccessRate}. Optional requires effect within ±${assessment.policy.optionalEffectTolerance}; supporting requires a larger reduction below the mandatory-effect threshold. Failed gates and contradictory outcomes remain unresolved. These are experimental decision thresholds, not statistical proof of necessity.`),
            guidedText('p', '', 'Every service must pass the sample, baseline-stability and intervention-confirmation gates. Arrows remain observed calls unless an edge has its own intervention evidence.'),
            guidedText('pre', '', JSON.stringify({policy: assessment.policy, services: assessment.services, edgeAssessments: assessment.edges || []}, null, 2))));
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
        const canvas = document.createElement('div');
        canvas.append(guidedText('p', 'workspace-status', 'Loading complete classification evidence…'));
        target.append(canvas, classificationKey(), guidedText('p', 'workspace-help', 'Supporting services remain visible here; the downstream graph contract excludes them. The entry service is structural, not an outage-tested dependency. Download test results for the complete classification evidence.'), button('Download graph JSON', () => downloadJsonArtifact(`${graph.workflowId}-workflow-graph.json`, graph)), guidedText('p', '', `Reviewed by ${approval.approvedBy}: ${approval.rationale}`));
        if (verification) target.append(guidedText('p', 'workspace-help', `Outage verification: ${verification.verifiedTrials}/${verification.eligibleTrials} runs verified by the backend; ${verification.verification.replaceAll('_', ' ')}.`));
        const evidence = details('View test results', guidedText('p', '', 'Loading saved results…'));
        target.append(evidence);
        requestScenario(`/api/workflows/dependency-assessments/${encodeURIComponent(approval.assessmentId)}`).then(assessment => {
            if (!target.isConnected) return;
            if (assessment.assessmentId !== approval.assessmentId || assessment.workflowId !== approval.workflowId) throw new Error('The assessment does not match this graph.');
            // The downstream contract excludes supporting nodes and their calls.
            // Use the saved assessment for the complete presentation graph.
            const displayGraph = assessmentGraph(assessment);
            const calls = document.createElement('div');
            renderGraph(calls, displayGraph);
            canvas.replaceChildren(renderDependencyMap(displayGraph, assessment), details('Service-to-service call graph', calls));
            evidence.replaceChildren(guidedText('summary', '', 'View test results'), assessmentRows(assessment), calculationDetails(assessment), button('Download test results JSON', () => downloadJsonArtifact(`${graph.workflowId}-test-results.json`, assessment), true));
        }).catch(error => { if (target.isConnected) { canvas.replaceChildren(guidedText('p', 'is-error', `Classification evidence unavailable: ${error.message}`)); evidence.append(guidedText('p', 'is-error', error.message)); } });
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
                offlineResult.append(renderDependencyMap({observedServices: result.serviceEvidence.map(item => item.service), edges: result.candidateGraph.edges, entryService: definition.entryService}, null, 'Uploaded trace evidence · classifications pending'), details('Service-to-service call graph', canvas));
            }
            const list = document.createElement('ul');
            result.serviceEvidence.forEach(item => list.append(guidedText('li', '', `${item.service}: ${item.traceCount}/${result.successfulTraceCount} successful traces (${(item.frequency * 100).toFixed(1)}%) · ${item.classification.replaceAll('_', ' ').toLowerCase()}`)));
            offlineResult.append(list, details('Analysis details', ...result.limitations.map(text => guidedText('p', '', text)), guidedText('pre', '', JSON.stringify(result, null, 2))), button('Download analysis JSON', () => downloadJsonArtifact(`${result.workflowId}-analysis.json`, result), true));
            offlineResult.hidden = false;
            message(offlineStatus, `${result.matchedTraceCount} matching traces analyzed. ${result.unrelatedTraceCount} traces belonged to other workflows.`);
        } catch (error) { if (version === offlineVersion) message(offlineStatus, error.message, true); }
        finally { submit.disabled = false; }
    }
    const graphPalette = {mandatory: '#8471e9', optional: '#279f86', supporting: '#3999d8', unresolved: '#8191a6',
        observed: '#8191a6', entry: 'var(--color-primary)'};
    function renderGraph(container, workflow) {
        container.innerHTML = "";
        container.className = "workflow-dag-container";
        container.tabIndex = 0;
        container.setAttribute("role", "region");
        container.setAttribute("aria-label", "Scrollable service call graph");

        const edges = workflow.edges || [];
        const mandatorySet = new Set(workflow.mandatoryServices || []);
        const optionalSet = new Set(workflow.optionalServices || []);
        const supportingSet = new Set(workflow.supportingServices || []);
        const unresolvedSet = new Set(workflow.unresolvedServices || []);
        const edgeKey = edge => JSON.stringify([edge.source, edge.target]);
        const mandatoryEdges = new Set((workflow.mandatoryEdges || []).map(edgeKey));
        const optionalEdges = new Set((workflow.optionalEdges || []).map(edgeKey));
        const supportingEdges = new Set((workflow.supportingEdges || []).map(edgeKey));
        (workflow.edgeAssessments || []).forEach(edge => {
            const key = JSON.stringify([edge.sourceService, edge.targetService]);
            if (edge.classification === 'mandatory') mandatoryEdges.add(key);
            if (edge.classification === 'optional') optionalEdges.add(key);
            if (edge.classification === 'supporting') supportingEdges.add(key);
        });

        // Collect all unique nodes
        const allNodesSet = new Set([
            ...(workflow.observedServices || []),
            ...(workflow.mandatoryServices || []),
            ...(workflow.optionalServices || []),
            ...supportingSet,
            ...unresolvedSet,
            ...(workflow.entryService ? [workflow.entryService] : []),
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
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#8191a6"></path>
            </marker>
            <marker id="dag-arrow-mandatory" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#8471e9"></path>
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

            const isMandatory = mandatoryEdges.has(edgeKey(edge));
            const isOptional = optionalEdges.has(edgeKey(edge));
            const isSupporting = supportingEdges.has(edgeKey(edge));
            const strokeColor = isMandatory ? graphPalette.mandatory : isOptional ? graphPalette.optional : isSupporting ? graphPalette.supporting : graphPalette.observed;
            const markerId = isMandatory ? "dag-arrow-mandatory" : "dag-arrow";

            const path = document.createElementNS(ns, "path");
            const cX = (startX + endX) / 2;
            path.setAttribute("d", `M ${startX} ${startY} C ${cX} ${startY}, ${cX} ${endY}, ${endX} ${endY}`);
            path.setAttribute("stroke", strokeColor);
            path.setAttribute("stroke-width", "2");
            path.setAttribute("fill", "none");
            if (isOptional || isSupporting) path.setAttribute("stroke-dasharray", "5,4");
            path.setAttribute("class", "dag-edge-path");
            path.setAttribute("marker-end", `url(#${markerId})`);
            path.dataset.source = edge.source;
            path.dataset.target = edge.target;
            path.dataset.mandatory = isMandatory ? "true" : "false";
            path.dataset.stroke = strokeColor;
            path.dataset.marker = markerId;
            const title = document.createElementNS(ns, 'title');
            title.textContent = `${edge.source} → ${edge.target}: ${isMandatory ? 'mandatory edge' : isOptional ? 'optional edge' : isSupporting ? 'supporting edge' : 'observed call; edge necessity unresolved'}`;
            path.append(title);

            edgesGroup.appendChild(path);
        });
        svg.appendChild(edgesGroup);

        // Nodes group
        const nodesGroup = document.createElementNS(ns, "g");
        nodesGroup.classList.add("dag-nodes-layer");

        allNodes.forEach((nodeName) => {
            const pos = positions.get(nodeName);
            if (!pos) return;

            const isEntry = workflow.entryService === nodeName;
            const isMandatory = mandatorySet.has(nodeName);
            const isOptional = optionalSet.has(nodeName);

            // One palette for the whole page (tiles, badges, graph); fills are tinted from the theme surface.
            let kind = 'mandatory';
            if (isEntry) kind = 'entry';
            else if (supportingSet.has(nodeName)) kind = 'supporting';
            else if (isOptional) kind = 'optional';
            else if (!isMandatory) kind = unresolvedSet.has(nodeName) ? 'unresolved' : 'observed';
            const accent = graphPalette[kind];
            const badgeText = kind === 'observed' ? 'NOT TESTED' : kind.toUpperCase();
            const dashed = kind === 'optional' || kind === 'supporting';

            const g = document.createElementNS(ns, "g");
            g.setAttribute("class", "dag-node");
            g.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);
            g.dataset.service = nodeName;

            const rect = document.createElementNS(ns, "rect");
            rect.setAttribute("width", String(nodeW));
            rect.setAttribute("height", String(nodeH));
            rect.setAttribute("rx", "8");
            rect.style.fill = `color-mix(in srgb, ${accent} 16%, var(--color-surface))`;
            rect.style.stroke = accent;
            rect.setAttribute("stroke-width", "1.5");
            rect.setAttribute("class", "dag-node-rect");
            if (dashed) rect.setAttribute("stroke-dasharray", "4,3");

            const nameText = document.createElementNS(ns, "text");
            nameText.setAttribute("x", "12");
            nameText.setAttribute("y", "20");
            nameText.style.fill = 'var(--color-text)';
            nameText.setAttribute("font-size", "12");
            nameText.setAttribute("font-weight", "700");
            nameText.setAttribute("font-family", "system-ui, -apple-system, sans-serif");
            nameText.textContent = nodeName;

            const badge = document.createElementNS(ns, "text");
            badge.setAttribute("x", "12");
            badge.setAttribute("y", "36");
            badge.style.fill = accent;
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
                    p.setAttribute("stroke", p.dataset.stroke);
                    p.setAttribute("stroke-width", "2");
                    p.style.opacity = "1";
                    p.setAttribute("marker-end", `url(#${p.dataset.marker})`);
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
    $('stop-workflow-scan').addEventListener('click', () => {
        stopDiscoveryScan();
        setGuidedStatus('Scan stopped. The displayed candidates are partial results. Choose Find workflows to start a new search.');
    });
    guidedForm.addEventListener('submit', connectGuidedSource);
    $('guided-auto-discover').addEventListener('click', findWorkflowsAutomatically);
    $('trace-start-confirm').addEventListener('click', () => runTraceServiceAction('start'));
    $('trace-stop-button').addEventListener('click', () => runTraceServiceAction('stop'));
    $('trace-start-cancel').addEventListener('click', () => {
        $('trace-start-prompt').hidden = true;
        message($('find-workflows-status'), 'Nothing was started. Discovery will retry while this page is open.');
    });
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
    $('new-workflow').addEventListener('click', collectNewEvidence);
    $('open-app').addEventListener('click', () => {
        const input = $('app-url');
        if (!input.value.trim() || !input.checkValidity()) { message($('app-url-source'), 'Enter the address your application is served at, such as http://localhost:<port>.', true); input.focus(); return; }
        openApplication(input.value.trim());
    });
    $('app-url').addEventListener('change', () => { if ($('app-url').checkValidity()) remember(applicationKey, $('app-url').value.trim()); });
    $('refresh-saved').addEventListener('click', () => loadSavedWorkflows());
    $('saved-workflows').addEventListener('change', () => {
        const selected = workflowCatalog.find(item => item.campaignId === $('saved-workflows').value);
        if (selected) renderRecordedCampaign(selected);
    });
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
