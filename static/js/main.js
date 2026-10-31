(function(){
    const manifestLink=document.createElement('link');
    manifestLink.rel='manifest';
    manifestLink.href='/static/manifest.webmanifest';
    document.head.appendChild(manifestLink);

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
    });
})();
