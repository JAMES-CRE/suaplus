const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/assessment.js'), 'utf8');

function assessment(radios) {
    const events = {};
    const elements = {
        'assessment-progress': {},
        'assessment-progress-text': {},
        'assessment-submit-hint': {},
        'sas-form': { querySelectorAll: () => radios, addEventListener: (name, callback) => { events[name] = callback; } },
    };
    vm.runInNewContext(source, {
        document: { getElementById: id => elements[id] },
        window: { addEventListener: (name, callback) => { events[name] = callback; } },
    });
    return { elements, events };
}

test('progress counts skill groups and updates when answers change', () => {
    const radios = [
        { name: 'skill_1', checked: false }, { name: 'skill_1', checked: false },
        { name: 'skill_2', checked: false },
    ];
    const { elements, events } = assessment(radios);
    assert.equal(elements['assessment-progress'].max, 2);
    assert.equal(elements['assessment-progress'].value, 0);
    radios[0].checked = true;
    events.change();
    assert.equal(elements['assessment-progress-text'].textContent, '1 of 2 skills assessed');
    assert.equal(elements['assessment-submit-hint'].textContent, '1 skill left to assess.');
    radios[0].checked = false;
    radios[1].checked = true;
    radios[2].checked = true;
    events.change();
    assert.equal(elements['assessment-progress'].value, 2);
    assert.equal(elements['assessment-submit-hint'].textContent, 'All questions answered. Your assessment is ready to save.');
});

test('restored browser answers are reflected on pageshow', () => {
    const radios = [{ name: 'skill_1', checked: false }];
    const { elements, events } = assessment(radios);
    radios[0].checked = true;
    events.pageshow();
    assert.equal(elements['assessment-progress'].value, 1);
});

test('other pages do not initialize assessment behavior', () => {
    vm.runInNewContext(source, { document: { getElementById: () => null } });
});

function draftAssessment({ key='token-and-revision-hash', saved=null, checked=false, consentChecked=false, blocked=false } = {}) {
    const events={};
    const storage=new Map();
    if(saved)storage.set('sua.assessment.'+key,JSON.stringify(saved));
    const radios=[{name:'comp_1',value:'3',checked}];
    const consent={checked:consentChecked};
    const elements={
        'assessment-progress':{}, 'assessment-progress-text':{}, 'assessment-submit-hint':{},
        'assessment-draft-notice':{},
        'assessment-discard':{addEventListener:(name,fn)=>{events.discard=fn;}},
        'sas-form':{dataset:{draftKey:key},querySelectorAll:()=>radios,querySelector:()=>consent,addEventListener:(name,fn)=>{events[name]=fn;}},
    };
    const sessionStorage={
        getItem:k=>{if(blocked)throw Error('blocked');return storage.get(k)||null;},
        setItem:(k,v)=>{if(blocked)throw Error('blocked');storage.set(k,v);},
        removeItem:k=>{if(blocked)throw Error('blocked');storage.delete(k);},
    };
    vm.runInNewContext(source,{document:{getElementById:id=>elements[id]},window:{sessionStorage,addEventListener:(name,fn)=>{events[name]=fn;}}});
    return {events,storage,radios,consent,elements,sessionStorage};
}

test('same-tab unexpired draft restores only available ratings',()=>{
    const state=draftAssessment({saved:{expires:Date.now()+100000,answers:{comp_1:'3'}}});
    assert.equal(state.radios[0].checked,true);
    assert.match(state.elements['assessment-draft-notice'].textContent,/restored/);
    assert.match(state.elements['assessment-submit-hint'].textContent,/Confirm consent/);
    state.consent.checked=true;state.events.change();
    assert.match(state.elements['assessment-submit-hint'].textContent,/ready to save/);
    const saved=JSON.parse(state.storage.get('sua.assessment.token-and-revision-hash'));
    assert.deepEqual(Object.keys(saved.answers),['comp_1']);
    assert.equal(saved.answers.consent,undefined);
});

test('expired draft is discarded and blocked storage stays usable',()=>{
    const expired=draftAssessment({saved:{expires:Date.now()-1,answers:{comp_1:'3'}}});
    assert.equal(expired.radios[0].checked,false);
    assert.equal(expired.storage.size,0);
    const blocked=draftAssessment({blocked:true});
    assert.match(blocked.elements['assessment-draft-notice'].textContent,/unavailable/);
    blocked.radios[0].checked=true;blocked.events.change();
    assert.equal(blocked.elements['assessment-progress'].value,1);
});

test('discard resets ratings and successful completion clears the matching draft',()=>{
    const state=draftAssessment({checked:true});
    state.events.change();
    assert.equal(state.storage.size,1);
    state.events.discard();
    assert.equal(state.radios[0].checked,false);
    assert.equal(state.storage.size,0);
    state.storage.set('sua.assessment.completed-hash','saved');
    state.storage.set('sua.assessment.another-tab-hash','other');
    vm.runInNewContext(source,{document:{getElementById:id=>id==='assessment-complete'?{dataset:{draftKey:'completed-hash'}}:null},window:{sessionStorage:state.sessionStorage}});
    assert.equal(state.storage.has('sua.assessment.completed-hash'),false);
    assert.equal(state.storage.has('sua.assessment.another-tab-hash'),true);
});

test('question-set or token fingerprint isolates draft state',()=>{
    const state=draftAssessment({key:'new-version',saved:{expires:Date.now()+100000,answers:{comp_999:'3'}}});
    assert.equal(state.radios[0].checked,false);
});
