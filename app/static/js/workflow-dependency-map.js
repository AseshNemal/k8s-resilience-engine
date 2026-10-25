/* Component 1 only. Display saved classification evidence; never infer labels from call frequency. */
(function () {
    'use strict';
    const groups = [
        {key: 'mandatory', label: 'Mandatory', description: 'Meets the recorded necessity decision rule.'},
        {key: 'supporting', label: 'Supportive', description: 'Supporting in the assessment: intermediate success-rate reduction.'},
        {key: 'optional', label: 'Optional', description: 'No material success-rate reduction in the recorded tests.'},
        {key: 'unresolved', label: 'Unresolved', description: 'No conclusive classification evidence for these services.'},
    ];
    const validName = value => typeof value === 'string' && value.trim().length > 0;

    function buildModel(input) {
        const names = new Set((input.observedServices || []).filter(validName));
        const assessments = new Map();
        for (const item of input.assessments || []) {
            if (!validName(item.serviceName)) continue;
            names.add(item.serviceName);
            const old = assessments.get(item.serviceName);
            assessments.set(item.serviceName, old && old.classification !== item.classification
                ? {serviceName: item.serviceName, classification: 'insufficient_evidence', reasons: ['Conflicting saved classifications; review the assessment.']}
                : item);
        }
        for (const edge of input.edges || []) {
            if (validName(edge.source)) names.add(edge.source);
            if (validName(edge.target)) names.add(edge.target);
        }
        const entryService = validName(input.entryService) ? input.entryService : null;
        if (entryService) names.add(entryService);
        const services = [...names].sort().map(name => {
            const assessment = assessments.get(name);
            // Structural graph mandatory slots and frequency candidates are not assessments.
            const classification = ['mandatory', 'supporting', 'optional'].includes(assessment?.classification)
                ? assessment.classification : 'unresolved';
            const traceIds = new Set();
            for (const edge of input.edges || []) {
                if (edge.source !== name && edge.target !== name) continue;
                for (const evidence of edge.evidence || []) if (validName(evidence.traceId)) traceIds.add(evidence.traceId);
            }
            return {name, classification: name === entryService ? 'entry' : classification,
                assessment: assessment || null, traceIds: [...traceIds].sort()};
        });
        return {entryService, services, groups: groups.map(group => ({...group,
            services: services.filter(service => service.classification === group.key)}))};
    }

    function render(container, input) {
        container._workflowMap?.destroy();
        container.replaceChildren();
        const doc = container.ownerDocument || document;
        const model = buildModel(input);
        const node = (tag, className, text) => {
            const element = doc.createElement(tag);
            element.className = className;
            if (text !== undefined) element.textContent = text;
            return element;
        };
        const root = node('section', 'workflow-dependency-map');
        root.setAttribute('aria-label', 'Workflow service dependency classifications');
        const heading = node('div', 'wdm-heading');
        heading.append(node('h4', '', 'Workflow dependency map'), node('span', 'wdm-source', input.sourceLabel || 'Observed trace evidence'));
        root.append(heading, node('p', 'wdm-explanation', 'Select a service to inspect its evidence. Lines connect services to classification groups; they are not service calls.'));
        if (model.entryService) root.append(node('p', 'wdm-entry-note', `Workflow entry: ${model.entryService} · structural entry point, not an outage-tested dependency`));
        const body = node('div', 'wdm-body');
        const servicesColumn = node('div', 'wdm-services');
        servicesColumn.append(node('h5', 'wdm-column-title', 'Services'));
        const classesColumn = node('div', 'wdm-groups');
        classesColumn.append(node('h5', 'wdm-column-title', 'Dependency classification'));
        const ns = 'http://www.w3.org/2000/svg';
        const svg = doc.createElementNS(ns, 'svg');
        svg.setAttribute('class', 'wdm-lines');
        svg.setAttribute('aria-hidden', 'true');
        const sourceNodes = new Map(), targetNodes = new Map(), serviceButtons = new Map(), paths = new Map();
        const evidencePanel = node('section', 'wdm-evidence');
        evidencePanel.setAttribute('aria-label', 'Selected service evidence');
        evidencePanel.setAttribute('aria-live', 'polite');
        evidencePanel.append(node('p', '', 'Choose a service card or a service inside a group to see the recorded evidence.'));
        const labelFor = key => key === 'entry' ? 'Entry point' : groups.find(group => group.key === key).label;
        let selected = null;
        function select(service) {
            selected = service.name;
            for (const [name, buttons] of serviceButtons) for (const button of buttons) button.setAttribute('aria-pressed', String(name === selected));
            for (const [name, path] of paths) path.style.opacity = name === selected ? '1' : '0.16';
            const assessment = service.assessment;
            evidencePanel.replaceChildren(node('h5', '', service.name), node('span', `wdm-badge wdm-${service.classification}`, labelFor(service.classification)));
            const cohort = value => value ? `${value.succeededRecords}/${value.eligibleRecords} eligible runs successful` : 'No assessment cohort available';
            const facts = node('dl', 'wdm-facts');
            const rows = service.classification === 'entry' ? [['Role', 'Workflow entry; no necessity classification inferred.']] : [
                ['Baseline', cohort(assessment?.baseline)], ['During unavailability', cohort(assessment?.intervention)],
                ['Success-rate drop', assessment?.failureEffect == null ? 'Not computed' : `${(assessment.failureEffect * 100).toFixed(1)} percentage points`],
            ];
            rows.push(['Trace evidence', service.traceIds.length ? `${service.traceIds.length} unique traces supporting recorded calls involving this service` : 'See the recorded-call evidence; trace identities are not available in this view.']);
            rows.forEach(([term, value]) => facts.append(node('dt', '', term), node('dd', '', value)));
            evidencePanel.append(facts);
            const reasons = node('ul', 'wdm-reasons');
            const messages = assessment?.reasons?.length ? assessment.reasons : service.classification === 'entry'
                ? ['The entry role comes from the workflow definition.']
                : ['Observed participation does not prove necessity. No classification assessment is saved for this service.'];
            if (assessment?.classification === 'conditional') reasons.append(node('li', '', 'Classification depends on business conditions; it remains unresolved in this overall view.'));
            messages.forEach(reason => reasons.append(node('li', '', reason)));
            evidencePanel.append(reasons);
            if (service.traceIds.length) {
                const traces = node('details', 'wdm-trace-ids');
                traces.append(node('summary', '', 'Supporting trace IDs'), node('pre', '', service.traceIds.join('\n')));
                evidencePanel.append(traces);
            }
        }
        function serviceButton(service, chip) {
            const button = node('button', `${chip ? 'wdm-chip' : 'wdm-service'} wdm-${service.classification}`);
            button.type = 'button';
            button.setAttribute('aria-label', `${service.name}: ${labelFor(service.classification)}. Inspect evidence`);
            button.setAttribute('aria-pressed', 'false');
            button.append(node('span', 'wdm-service-name', service.name));
            if (!chip) button.append(node('span', 'wdm-service-label', labelFor(service.classification)));
            button.addEventListener('click', () => select(service));
            if (!serviceButtons.has(service.name)) serviceButtons.set(service.name, []);
            serviceButtons.get(service.name).push(button);
            return button;
        }
        for (const service of model.services) {
            const button = serviceButton(service, false);
            servicesColumn.append(button);
            sourceNodes.set(service.name, button);
        }
        if (!model.services.length) servicesColumn.append(node('p', 'wdm-empty', 'No service evidence has been recorded yet.'));
        for (const group of model.groups) {
            const card = node('section', `wdm-group wdm-${group.key}`);
            card.setAttribute('aria-label', `${group.label}: ${group.services.length} services`);
            const title = node('div', 'wdm-group-title');
            title.append(node('h5', '', group.label), node('span', 'wdm-count', String(group.services.length)));
            card.append(title, node('p', 'wdm-group-description', group.description));
            const chips = node('div', 'wdm-chips');
            for (const service of group.services) {
                const chip = serviceButton(service, true);
                chips.append(chip);
                targetNodes.set(service.name, chip);
            }
            if (!group.services.length) chips.append(node('span', 'wdm-empty', 'None established in this evidence'));
            card.append(chips);
            classesColumn.append(card);
        }
        body.append(svg, servicesColumn, classesColumn);
        root.append(body, evidencePanel);
        container.append(root);
        let observer = null, frame = null, destroyed = false;
        function draw() {
            frame = null;
            if (destroyed || !root.isConnected) return;
            const bounds = body.getBoundingClientRect();
            svg.replaceChildren();
            paths.clear();
            if (!bounds.width || !bounds.height) return;
            svg.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
            for (const [name, target] of targetNodes) {
                const source = sourceNodes.get(name);
                const a = source.getBoundingClientRect(), b = target.getBoundingClientRect();
                // Stacked mobile layouts communicate membership through text and chips.
                if (b.left <= a.right) continue;
                const x1 = a.right - bounds.left, y1 = a.top + a.height / 2 - bounds.top;
                const x2 = b.left - bounds.left, y2 = b.top + b.height / 2 - bounds.top;
                const middle = x1 + (x2 - x1) / 2;
                const path = doc.createElementNS(ns, 'path');
                path.setAttribute('d', `M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`);
                path.setAttribute('class', `wdm-link wdm-${model.services.find(service => service.name === name).classification}`);
                path.dataset.service = name;
                path.style.opacity = selected && selected !== name ? '0.16' : '0.65';
                svg.append(path);
                paths.set(name, path);
            }
        }
        function schedule() {
            if (destroyed) return;
            if (window.requestAnimationFrame) {
                if (frame !== null) window.cancelAnimationFrame(frame);
                frame = window.requestAnimationFrame(draw);
            } else draw();
        }
        if (window.ResizeObserver) {
            observer = new window.ResizeObserver(() => {
                if (!root.isConnected) { observer.disconnect(); return; }
                schedule();
            });
            observer.observe(body);
        }
        schedule();
        const handle = {model, destroy() {
            destroyed = true;
            observer?.disconnect();
            if (frame !== null && window.cancelAnimationFrame) window.cancelAnimationFrame(frame);
        }};
        container._workflowMap = handle;
        return handle;
    }
    window.WorkflowDependencyMap = {buildModel, render};
})();
