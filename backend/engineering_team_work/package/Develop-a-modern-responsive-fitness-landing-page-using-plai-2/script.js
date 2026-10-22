document.addEventListener('DOMContentLoaded', () => {
    // Responsive Navigation (Hamburger Menu)
    const hamburgerMenu = document.querySelector('.hamburger-menu');
    const navLinks = document.querySelector('.nav-links');

    if (hamburgerMenu && navLinks) {
        hamburgerMenu.addEventListener('click', () => {
            navLinks.classList.toggle('nav-active');
            hamburgerMenu.classList.toggle('active'); // Optional: for animating hamburger icon
        });

        // Close nav menu when a link is clicked (for mobile)
        navLinks.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                if (navLinks.classList.contains('nav-active')) {
                    navLinks.classList.remove('nav-active');
                    hamburgerMenu.classList.remove('active');
                }
            });
        });
    }

    // FAQ Accordion
    const faqQuestions = document.querySelectorAll('.faq-question');

    faqQuestions.forEach(question => {
        question.addEventListener('click', () => {
            const answer = question.nextElementSibling; // The .faq-answer element

            // Toggle the 'answer-visible' class on the answer
            answer.classList.toggle('answer-visible');
            // Optional: Toggle 'active' class on question for styling (e.g., changing icon)
            question.classList.toggle('active');
        });
    });
});