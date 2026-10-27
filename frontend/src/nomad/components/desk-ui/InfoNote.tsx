import {useRef,type ReactNode} from 'react';

export function InfoNote({title='Information',children}:{title?:string;children:ReactNode}){
 const dialog=useRef<HTMLDialogElement>(null);
 return <><button type="button" className="info-note-button" aria-label={title} title={title} onClick={()=>dialog.current?.showModal()}>ⓘ</button>
 <dialog ref={dialog} className="info-note-dialog" aria-label={title} onClick={e=>{if(e.target===e.currentTarget)dialog.current?.close();}}>
 <div className="info-note-heading"><strong>{title}</strong><button type="button" aria-label="Close information" onClick={()=>dialog.current?.close()}>×</button></div>
 <div className="info-note-body">{children}</div></dialog></>;
}
