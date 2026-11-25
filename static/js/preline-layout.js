/**
 * Preline UI Application Layout Interactions
 * Controls mobile offcanvas sidebar drawer, hamburger toggle, backdrop,
 * keyboard accessibility (Esc, '/'), and client-side navigation search.
 */
(function () {
  'use strict';

  function initPrelineLayout() {
    var sidebar = document.getElementById('application-sidebar');
    var toggleBtn = document.getElementById('sidebar-toggle-btn');
    var closeBtn = document.getElementById('sidebar-close-btn');
    var backdrop = document.getElementById('sidebar-backdrop');
    var navSearch = document.getElementById('sidebar-nav-search');

    if (!sidebar) return;

    function openSidebar() {
      sidebar.classList.add('is-open');
      sidebar.setAttribute('aria-hidden', 'false');
      if (backdrop) backdrop.classList.add('is-open');
      if (toggleBtn) {
        toggleBtn.classList.add('is-active');
        toggleBtn.setAttribute('aria-expanded', 'true');
      }
      document.body.classList.add('sidebar-open');
    }

    function closeSidebar() {
      sidebar.classList.remove('is-open');
      sidebar.setAttribute('aria-hidden', 'true');
      if (backdrop) backdrop.classList.remove('is-open');
      if (toggleBtn) {
        toggleBtn.classList.remove('is-active');
        toggleBtn.setAttribute('aria-expanded', 'false');
      }
      document.body.classList.remove('sidebar-open');
    }

    function toggleSidebar() {
      if (sidebar.classList.contains('is-open')) {
        closeSidebar();
      } else {
        openSidebar();
      }
    }

    if (toggleBtn) {
      toggleBtn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        toggleSidebar();
      });
    }

    if (closeBtn) {
      closeBtn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        closeSidebar();
      });
    }

    if (backdrop) {
      backdrop.addEventListener('click', function () {
        closeSidebar();
      });
    }

    // Keyboard controls: Escape to close, '/' to focus search
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && sidebar.classList.contains('is-open')) {
        closeSidebar();
      }
      if (e.key === '/' && navSearch && document.activeElement !== navSearch) {
        var activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
        if (activeTag !== 'input' && activeTag !== 'textarea') {
          e.preventDefault();
          if (window.innerWidth < 1024) {
            openSidebar();
          }
          navSearch.focus();
        }
      }
    });

    // Auto-close drawer on small screens when clicking any navigation link
    var navLinks = sidebar.querySelectorAll('.nav-item, .section-item, .foot-link');
    for (var i = 0; i < navLinks.length; i++) {
      navLinks[i].addEventListener('click', function () {
        if (window.innerWidth < 1024) {
          closeSidebar();
        }
      });
    }

    // Ensure body scroll is unlocked if viewport is resized to desktop width
    window.addEventListener('resize', function () {
      if (window.innerWidth >= 1024 && sidebar.classList.contains('is-open')) {
        closeSidebar();
      }
    });

    // Sidebar navigation instant search / filter
    if (navSearch) {
      navSearch.addEventListener('input', function () {
        var query = navSearch.value.trim().toLowerCase();
        var items = sidebar.querySelectorAll('.nav-item, .section-item');
        var groups = sidebar.querySelectorAll('.sidebar-nav-group, .sidebar-section');

        if (!query) {
          for (var j = 0; j < items.length; j++) {
            items[j].style.display = '';
          }
          for (var k = 0; k < groups.length; k++) {
            groups[k].style.display = '';
          }
          return;
        }

        for (var m = 0; m < items.length; m++) {
          var text = items[m].textContent.toLowerCase();
          if (text.indexOf(query) !== -1) {
            items[m].style.display = '';
          } else {
            items[m].style.display = 'none';
          }
        }

        // Hide empty groups
        for (var n = 0; n < groups.length; n++) {
          var visibleItems = groups[n].querySelectorAll('.nav-item:not([style*="display: none"]), .section-item:not([style*="display: none"])');
          if (visibleItems.length === 0) {
            groups[n].style.display = 'none';
          } else {
            groups[n].style.display = '';
          }
        }
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPrelineLayout);
  } else {
    initPrelineLayout();
  }
})();
