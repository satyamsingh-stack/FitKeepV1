(function(){
    const appleMobileMeta=document.createElement('meta');
    appleMobileMeta.name='apple-mobile-web-app-capable';
    appleMobileMeta.content='yes';
    document.head.appendChild(appleMobileMeta);

    const installButton=document.createElement('button');
    installButton.type='button';
    installButton.className='btn btn-brand btn-sm ms-lg-2';
    installButton.hidden=true;
    installButton.innerHTML='<i class="bi bi-download me-1" aria-hidden="true"></i>Install app';
    installButton.setAttribute('aria-label','Install FitKeep app');

    let installPrompt=null;
    window.addEventListener('beforeinstallprompt',event=>{
        event.preventDefault();
        installPrompt=event;
        const nav=document.querySelector('.navbar-collapse');
        if(nav&&!installButton.isConnected)nav.appendChild(installButton);
        installButton.hidden=false;
    });

    installButton.addEventListener('click',async()=>{
        if(!installPrompt)return;
        await installPrompt.prompt();
        const choice=await installPrompt.userChoice;
        if(choice.outcome==='accepted')installButton.hidden=true;
        installPrompt=null;
    });

    window.addEventListener('appinstalled',()=>{
        installButton.hidden=true;
        installPrompt=null;
    });

    if('serviceWorker' in navigator){
        window.addEventListener('load',()=>{
            navigator.serviceWorker.register('/service-worker.js',{scope:'/'})
                .catch(error=>console.error('FitKeep service worker registration failed:',error));
        });
    }

    document.addEventListener('DOMContentLoaded',function(){
        const d=document.querySelector('[data-current-date]');
        if(d)d.textContent=new Intl.DateTimeFormat(undefined,{weekday:'short',month:'short',day:'numeric'}).format(new Date());
        document.querySelectorAll('[data-confirm]').forEach(e=>e.addEventListener('click',event=>{
            if(!window.confirm(e.getAttribute('data-confirm')||'Are you sure?')){
                event.preventDefault();
                event.stopPropagation();
            }
        }));
        document.querySelectorAll('[data-auto-submit]').forEach(e=>e.addEventListener('change',()=>{
            if(e.form)e.form.submit();
        }));
        document.querySelectorAll('[data-copy-target]').forEach(b=>b.addEventListener('click',async()=>{
            const t=document.querySelector(b.getAttribute('data-copy-target'));
            if(!t)return;
            try{
                await navigator.clipboard.writeText(t.value||t.textContent.trim());
                const old=b.innerHTML;
                b.innerHTML='<i class="bi bi-check2"></i> Copied';
                setTimeout(()=>b.innerHTML=old,1400);
            }catch(e){
                if(t.select)t.select();
            }
        }));

        // Auto-add and populate +91 for attendance and member phone inputs
        const phoneInputs = document.querySelectorAll('input[type="tel"], input[name="phone"], input[name="emergency_contact"], input#phone, input#id_phone, input#id_emergency_contact');
        phoneInputs.forEach(input => {
            const isAttendance = input.id === 'phone' || (input.form && (input.form.id === 'checkinForm' || input.form.action.includes('check-in')));
            
            function ensurePrefix() {
                const v = input.value.trim();
                if (!v || v === '+91') {
                    input.value = '+91 ';
                }
            }
            ensurePrefix();

            input.addEventListener('focus', function () {
                if (!this.value.trim() || this.value.trim() === '+91') {
                    this.value = '+91 ';
                }
                if (this.value.startsWith('+91')) {
                    const len = this.value.length;
                    setTimeout(() => this.setSelectionRange(len, len), 10);
                }
            });

            input.addEventListener('click', function () {
                if (this.value === '+91 ') {
                    this.setSelectionRange(4, 4);
                }
            });

            input.addEventListener('keydown', function (e) {
                if (e.key === 'Backspace' && this.value === '+91 ') {
                    e.preventDefault();
                }
            });

            input.addEventListener('input', function () {
                let v = this.value;
                if (isAttendance && /^[a-zA-Z]/.test(v.trim())) {
                    return; // allow entering Member ID
                }
                let digits = v.replace(/\D/g, '');
                if (digits.startsWith('91')) {
                    digits = digits.slice(2);
                } else if (digits.startsWith('0')) {
                    digits = digits.slice(1);
                }
                if (digits.length > 0) {
                    this.value = '+91 ' + digits.slice(0, 10);
                } else if (v.startsWith('+') || v.trim() === '') {
                    this.value = '+91 ';
                }
            });
        });
    });
})();
