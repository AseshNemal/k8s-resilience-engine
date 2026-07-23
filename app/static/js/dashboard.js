const statusElement = document.querySelector("#system-status");


async function checkSystem() {
    try {
        const response = await fetch("/api/health");

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        statusElement.textContent = data.status;
        statusElement.classList.add("healthy");
    } catch (error) {
        statusElement.textContent = "System unavailable";
        statusElement.classList.add("error");

        console.error("Health check failed:", error);
    }
}


checkSystem();