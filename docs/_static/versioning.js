// Version selector functionality for Litestar MCP documentation
document.addEventListener('DOMContentLoaded', function() {
    // Create version selector if it doesn't exist
    function createVersionSelector() {
        const versionData = {
            versions: ['latest'],
            current: 'latest'
        };

        // Try to load version data from versions.json
        fetch('/_static/versions.json')
            .then(response => response.ok ? response.json() : versionData)
            .then(data => {
                if (data.versions && data.versions.length > 1) {
                    insertVersionSelector(data);
                }
            })
            .catch(() => {
                // Fallback to default if versions.json is not available
                console.log('No versions.json found, using default version data');
            });
    }

    function insertVersionSelector(versionData) {
        const selector = document.createElement('select');
        selector.className = 'version-selector';
        selector.setAttribute('aria-label', 'Version selector');

        versionData.versions.forEach(version => {
            const option = document.createElement('option');
            option.value = version;
            option.textContent = version === 'latest' ? 'Latest' : `v${version}`;
            if (version === versionData.current) {
                option.selected = true;
            }
            selector.appendChild(option);
        });

        selector.addEventListener('change', function(event) {
            const selectedVersion = event.target.value;
            const currentPath = window.location.pathname;
            const pathParts = currentPath.split('/').filter(part => part);

            // Remove current version from path if present
            if (pathParts.length > 0 && (pathParts[0] === 'latest' || versionData.versions.includes(pathParts[0]))) {
                pathParts.shift();
            }

            // Construct new URL
            const newPath = selectedVersion === 'latest'
                ? `/latest/${pathParts.join('/')}`
                : `/${selectedVersion}/${pathParts.join('/')}`;

            window.location.href = newPath;
        });

        // Insert the version selector into the navigation
        const nav = document.querySelector('.sy-nav');
        if (nav) {
            const versionContainer = document.createElement('div');
            versionContainer.className = 'version-selector-container';
            versionContainer.style.padding = '0.5rem';
            versionContainer.appendChild(selector);
            nav.appendChild(versionContainer);
        }
    }

    // Initialize version selector
    createVersionSelector();

    // Add some utility functions for documentation navigation
    function enhanceNavigation() {
        // Add keyboard shortcuts for navigation
        document.addEventListener('keydown', function(event) {
            if (event.altKey) {
                switch(event.key) {
                    case 'h':
                        event.preventDefault();
                        window.location.href = '/';
                        break;
                    case 'g':
                        event.preventDefault();
                        const searchInput = document.querySelector('input[type="search"]');
                        if (searchInput) {
                            searchInput.focus();
                        }
                        break;
                }
            }
        });

        // Add smooth scrolling to anchor links
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', function (e) {
                e.preventDefault();
                const target = document.querySelector(this.getAttribute('href'));
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            });
        });
    }

    // Initialize navigation enhancements
    enhanceNavigation();
});

// Export for use in other scripts if needed
window.LitestarMCPDocs = {
    version: '1.0.0',
    features: {
        versionSelector: true,
        keyboardShortcuts: true,
        smoothScrolling: true
    }
};
