// ============================================
// RISK GUARDIAN — Landing Page Interactions
// ============================================

document.addEventListener('DOMContentLoaded', () => {

  // ====== CURSOR GLOW ======
  const glow = document.getElementById('cursorGlow');
  let mouseX = 0, mouseY = 0, glowX = 0, glowY = 0;

  document.addEventListener('mousemove', (e) => {
    mouseX = e.clientX;
    mouseY = e.clientY;
  });

  function animateGlow() {
    glowX += (mouseX - glowX) * 0.08;
    glowY += (mouseY - glowY) * 0.08;
    glow.style.left = glowX + 'px';
    glow.style.top = glowY + 'px';
    requestAnimationFrame(animateGlow);
  }
  animateGlow();

  // ====== SCROLL REVEAL ======
  const reveals = document.querySelectorAll('.reveal');

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        // Handle staggered delays for feature cards
        const delay = entry.target.dataset.delay;
        if (delay !== undefined) {
          entry.target.style.setProperty('--delay', delay);
        }
        entry.target.classList.add('visible');
      }
    });
  }, {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
  });

  reveals.forEach(el => observer.observe(el));

  // ====== COUNTER ANIMATION ======
  const counters = document.querySelectorAll('.stat-num[data-count]');

  const counterObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const target = parseInt(entry.target.dataset.count);
        animateCounter(entry.target, target);
        counterObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });

  counters.forEach(el => counterObserver.observe(el));

  function animateCounter(el, target) {
    let current = 0;
    const increment = Math.ceil(target / 30);
    const timer = setInterval(() => {
      current += increment;
      if (current >= target) {
        current = target;
        clearInterval(timer);
      }
      el.textContent = current;
    }, 40);
  }

  // ====== SMOOTH NAV ======
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', (e) => {
      e.preventDefault();
      const target = document.querySelector(anchor.getAttribute('href'));
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });

  // ====== NAV SCROLL EFFECT ======
  const nav = document.querySelector('.nav');
  window.addEventListener('scroll', () => {
    if (window.scrollY > 50) {
      nav.style.borderBottomColor = 'rgba(255,255,255,0.1)';
      nav.style.background = 'rgba(6,8,15,0.95)';
    } else {
      nav.style.borderBottomColor = 'rgba(255,255,255,0.06)';
      nav.style.background = 'rgba(6,8,15,0.8)';
    }
  });

  // ====== TOAST NOTIFICATIONS ======
  function showToast(message) {
    const toast = document.getElementById('toast');
    const text = document.getElementById('toastText');
    text.textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
  }

  // ====== DOWNLOAD BUTTON (ATAS) ======
  const btnAtas = document.getElementById('btn-atas');
  if (btnAtas) {
    btnAtas.addEventListener('click', (e) => {
      e.preventDefault();
      showToast('Download starting... Check your downloads folder.');
      window.location.href = '/downloads/xtrade-ai-atas.zip';
    });
  }

  // ====== NOTIFY BUTTONS ======
  document.querySelectorAll('.btn-notify').forEach(btn => {
    btn.addEventListener('click', () => {
      const platform = btn.dataset.platform;
      const names = {
        ninjatrader: 'NinjaTrader',
        tradovate: 'Tradovate',
        projectx: 'Project X',
        pro: 'Pro plan'
      };

      // Toggle state
      if (btn.classList.contains('notified')) return;
      btn.classList.add('notified');
      btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg><span>You're on the list!</span>`;
      btn.style.borderColor = 'rgba(34,197,94,0.4)';
      btn.style.color = '#22c55e';

      showToast(`You'll be notified when ${names[platform] || platform} is ready!`);

      // TODO: Send to backend / email list
      // fetch('/api/notify', {
      //   method: 'POST',
      //   body: JSON.stringify({ platform, email: '...' })
      // });
    });
  });

  // ====== DASHBOARD LIVE SIMULATION ======
  // Simulate live P&L updates in the hero dashboard
  const dashValues = document.querySelectorAll('.dash-value');
  const progressFill = document.querySelector('.dash-progress-fill');

  function simulateDashboard() {
    // Subtle P&L fluctuations
    const pnlEl = dashValues[1]; // Daily P&L
    if (pnlEl) {
      const base = 847.50;
      const variation = (Math.random() - 0.45) * 30;
      const newPnl = base + variation;
      pnlEl.textContent = `+$${newPnl.toFixed(2)}`;
    }

    // Trailing DD fluctuation
    const ddEl = dashValues[3]; // Trailing DD
    if (ddEl) {
      const base = 382;
      const variation = Math.floor((Math.random() - 0.45) * 15);
      const newDD = base + variation;
      const pct = Math.round(newDD / 500 * 100);
      ddEl.textContent = `$${newDD} / $500 (${pct}%)`;
      ddEl.className = 'dash-value ' + (pct > 80 ? 'red' : pct > 60 ? 'orange' : 'cyan');
    }

    // Progress bar
    if (progressFill) {
      const base = 23;
      const variation = (Math.random() - 0.45) * 4;
      progressFill.style.width = (base + variation) + '%';
    }
  }

  setInterval(simulateDashboard, 2000);

  // ====== TILT EFFECT ON CARDS ======
  document.querySelectorAll('.feature-card, .platform-card').forEach(card => {
    card.addEventListener('mousemove', (e) => {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const centerX = rect.width / 2;
      const centerY = rect.height / 2;
      const rotateX = (y - centerY) / centerY * -3;
      const rotateY = (x - centerX) / centerX * 3;

      card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateY(-4px)`;
    });

    card.addEventListener('mouseleave', () => {
      card.style.transform = '';
    });
  });

  // ====== PARALLAX GRID ======
  const gridBg = document.querySelector('.grid-bg');
  window.addEventListener('scroll', () => {
    const scrolled = window.scrollY;
    gridBg.style.transform = `translateY(${scrolled * 0.15}px)`;
  });

});
