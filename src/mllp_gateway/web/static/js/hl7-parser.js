// HL7 segment descriptions
const SEGMENT_DESC = {
  MSH:"Message Header",EVN:"Event Type",PID:"Patient Identification",PD1:"Patient Additional Demographics",
  PV1:"Patient Visit",PV2:"Patient Visit - Additional",NK1:"Next of Kin",IN1:"Insurance",IN2:"Insurance Additional",
  GT1:"Guarantor",AL1:"Allergy",DG1:"Diagnosis",PR1:"Procedures",ROL:"Role",
  ORC:"Common Order",OBR:"Observation Request",OBX:"Observation Result",NTE:"Notes and Comments",
  TXA:"Transcription Document Header",RXA:"Pharmacy Administration",RXE:"Pharmacy Encoded Order",
  RXO:"Pharmacy Prescription Order",RXR:"Pharmacy Route",RXD:"Pharmacy Dispense",RXG:"Pharmacy Give",
  SCH:"Schedule Activity",AIS:"Appointment Information",AIG:"Appointment Information - General Resource",
  AIL:"Appointment Information - Location",AIP:"Appointment Information - Personnel",
  FT1:"Financial Transaction",ACC:"Accident",UB1:"UB82",UB2:"UB92 Data",
  MRG:"Merge Patient Information",QPD:"Query Parameter Definition",RCP:"Response Control Parameter",
  MSA:"Message Acknowledgment",ERR:"Error",QAK:"Query Acknowledgment",DSC:"Continuation Pointer",
  BHS:"Batch Header",BTS:"Batch Trailer",FHS:"File Header",FTS:"File Trailer",
  ZPI:"Custom Z-Segment (PID Extension)",ZPD:"Custom Z-Segment"
};

// HL7 field descriptions per segment
const FIELD_DESC = {
  MSH:{1:"Field Separator",2:"Encoding Characters",3:"Sending Application",4:"Sending Facility",
    5:"Receiving Application",6:"Receiving Facility",7:"Date/Time of Message",8:"Security",
    9:"Message Type",10:"Message Control ID",11:"Processing ID",12:"Version ID",
    13:"Sequence Number",14:"Continuation Pointer",15:"Accept Acknowledgment Type",
    16:"Application Acknowledgment Type",17:"Country Code",18:"Character Set",
    19:"Principal Language",20:"Alternate Character Set Handling",21:"Message Profile Identifier"},
  EVN:{1:"Event Type Code",2:"Recorded Date/Time",3:"Date/Time Planned Event",
    4:"Event Reason Code",5:"Operator ID",6:"Event Occurred",7:"Event Facility"},
  PID:{1:"Set ID",2:"Patient ID (External)",3:"Patient Identifier List",4:"Alternate Patient ID",
    5:"Patient Name",6:"Mother's Maiden Name",7:"Date/Time of Birth",8:"Administrative Sex",
    9:"Patient Alias",10:"Race",11:"Patient Address",12:"County Code",13:"Phone Number - Home",
    14:"Phone Number - Business",15:"Primary Language",16:"Marital Status",17:"Religion",
    18:"Patient Account Number",19:"SSN Number",20:"Driver's License Number",
    21:"Mother's Identifier",22:"Ethnic Group",23:"Birth Place",24:"Multiple Birth Indicator",
    25:"Birth Order",26:"Citizenship",27:"Veterans Military Status",28:"Nationality",
    29:"Patient Death Date/Time",30:"Patient Death Indicator"},
  PV1:{1:"Set ID",2:"Patient Class",3:"Assigned Patient Location",4:"Admission Type",
    5:"Preadmit Number",6:"Prior Patient Location",7:"Attending Doctor",8:"Referring Doctor",
    9:"Consulting Doctor",10:"Hospital Service",11:"Temporary Location",12:"Preadmit Test Indicator",
    13:"Re-admission Indicator",14:"Admit Source",15:"Ambulatory Status",16:"VIP Indicator",
    17:"Admitting Doctor",18:"Patient Type",19:"Visit Number",20:"Financial Class",
    21:"Charge Price Indicator",22:"Courtesy Code",23:"Credit Rating",24:"Contract Code",
    25:"Contract Effective Date",26:"Contract Amount",27:"Contract Period",28:"Interest Code",
    29:"Transfer to Bad Debt Code",30:"Transfer to Bad Debt Date",31:"Bad Debt Agency Code",
    32:"Bad Debt Transfer Amount",33:"Bad Debt Recovery Amount",34:"Delete Account Indicator",
    35:"Delete Account Date",36:"Discharge Disposition",37:"Discharged to Location",
    38:"Diet Type",39:"Servicing Facility",40:"Bed Status",41:"Account Status",
    42:"Pending Location",43:"Prior Temporary Location",44:"Admit Date/Time",
    45:"Discharge Date/Time",46:"Current Patient Balance",47:"Total Charges",
    48:"Total Adjustments",49:"Total Payments",50:"Alternate Visit ID",51:"Visit Indicator",
    52:"Other Healthcare Provider"},
  ORC:{1:"Order Control",2:"Placer Order Number",3:"Filler Order Number",4:"Placer Group Number",
    5:"Order Status",6:"Response Flag",7:"Quantity/Timing",8:"Parent",
    9:"Date/Time of Transaction",10:"Entered By",11:"Verified By",12:"Ordering Provider",
    13:"Enterer's Location",14:"Call Back Phone Number",15:"Order Effective Date/Time",
    16:"Order Control Code Reason",17:"Entering Organization",18:"Entering Device",
    19:"Action By",20:"Advanced Beneficiary Notice Code",21:"Ordering Facility Name",
    22:"Ordering Facility Address",23:"Ordering Facility Phone Number",24:"Ordering Provider Address"},
  OBR:{1:"Set ID",2:"Placer Order Number",3:"Filler Order Number",4:"Universal Service Identifier",
    5:"Priority",6:"Requested Date/Time",7:"Observation Date/Time",8:"Observation End Date/Time",
    9:"Collection Volume",10:"Collector Identifier",11:"Specimen Action Code",12:"Danger Code",
    13:"Relevant Clinical Info",14:"Specimen Received Date/Time",15:"Specimen Source",
    16:"Ordering Provider",17:"Order Callback Phone Number",18:"Placer Field 1",
    19:"Placer Field 2",20:"Filler Field 1",21:"Filler Field 2",22:"Results Rpt/Status Chng - Date/Time",
    23:"Charge to Practice",24:"Diagnostic Serv Sect ID",25:"Result Status",
    26:"Parent Result",27:"Quantity/Timing",28:"Result Copies To",29:"Parent",
    30:"Transportation Mode",31:"Reason for Study",32:"Principal Result Interpreter",
    33:"Assistant Result Interpreter",34:"Technician",35:"Transcriptionist",
    36:"Scheduled Date/Time",37:"Number of Sample Containers",38:"Transport Logistics of Collected Sample",
    39:"Collector's Comment",40:"Transport Arrangement Responsibility",41:"Transport Arranged",
    42:"Escort Required",43:"Planned Patient Transport Comment",44:"Procedure Code",
    45:"Procedure Code Modifier"},
  OBX:{1:"Set ID",2:"Value Type",3:"Observation Identifier",4:"Observation Sub-ID",
    5:"Observation Value",6:"Units",7:"Reference Range",8:"Interpretation Codes",
    9:"Probability",10:"Nature of Abnormal Test",11:"Observation Result Status",
    12:"Effective Date of Reference Range",13:"User Defined Access Checks",
    14:"Date/Time of Observation",15:"Producer's ID",16:"Responsible Observer",
    17:"Observation Method",18:"Equipment Instance Identifier",19:"Date/Time of Analysis"},
  NK1:{1:"Set ID",2:"Name",3:"Relationship",4:"Address",5:"Phone Number",6:"Business Phone Number",
    7:"Contact Role",8:"Start Date",9:"End Date",10:"Next of Kin Job Title",
    11:"Next of Kin Job Code/Class",12:"Next of Kin Employee Number",13:"Organization Name"},
  AL1:{1:"Set ID",2:"Allergen Type Code",3:"Allergen Code/Description",4:"Allergy Severity Code",
    5:"Allergy Reaction Code",6:"Identification Date"},
  DG1:{1:"Set ID",2:"Diagnosis Coding Method",3:"Diagnosis Code",4:"Diagnosis Description",
    5:"Diagnosis Date/Time",6:"Diagnosis Type",7:"Major Diagnostic Category",
    8:"Diagnostic Related Group",9:"DRG Approval Indicator",10:"DRG Grouper Review Code"},
  NTE:{1:"Set ID",2:"Source of Comment",3:"Comment",4:"Comment Type"},
  MSA:{1:"Acknowledgment Code",2:"Message Control ID",3:"Text Message",4:"Expected Sequence Number",
    5:"Delayed Acknowledgment Type",6:"Error Condition"},
  ERR:{1:"Error Code and Location",2:"Error Location",3:"HL7 Error Code",4:"Severity",
    5:"Application Error Code",6:"Application Error Parameter",7:"Diagnostic Information",
    8:"User Message",9:"Inform Person Indicator"},
  RXA:{1:"Give Sub-ID Counter",2:"Administration Sub-ID Counter",3:"Date/Time Start of Administration",
    4:"Date/Time End of Administration",5:"Administered Code",6:"Administered Amount",
    7:"Administered Units",8:"Administered Dosage Form",9:"Administration Notes",
    10:"Administering Provider",11:"Administered-at Location",12:"Administered Per (Time Unit)",
    13:"Administered Strength",14:"Administered Strength Units",15:"Substance Lot Number",
    16:"Substance Expiration Date",17:"Substance Manufacturer Name",18:"Substance/Treatment Refusal Reason",
    19:"Indication",20:"Completion Status",21:"Action Code - RXA",22:"System Entry Date/Time"},
  FT1:{1:"Set ID",2:"Transaction ID",3:"Transaction Batch ID",4:"Transaction Date",
    5:"Transaction Posting Date",6:"Transaction Type",7:"Transaction Code",8:"Transaction Description",
    9:"Transaction Description - Alt",10:"Transaction Quantity",11:"Transaction Amount - Extended",
    12:"Transaction Amount - Unit"},
  IN1:{1:"Set ID",2:"Insurance Plan ID",3:"Insurance Company ID",4:"Insurance Company Name",
    5:"Insurance Company Address",6:"Insurance Co Contact Person",7:"Insurance Co Phone Number",
    8:"Group Number",9:"Group Name",10:"Insured's Group Emp ID",11:"Insured's Group Emp Name",
    12:"Plan Effective Date",13:"Plan Expiration Date"},
  SCH:{1:"Placer Appointment ID",2:"Filler Appointment ID",3:"Occurrence Number",
    4:"Placer Group Number",5:"Schedule ID",6:"Event Reason",7:"Appointment Reason",
    8:"Appointment Type",9:"Appointment Duration",10:"Appointment Duration Units",
    11:"Appointment Timing Quantity",12:"Placer Contact Person",13:"Placer Contact Phone Number",
    14:"Placer Contact Address",15:"Placer Contact Location",16:"Filler Contact Person"},
  MRG:{1:"Prior Patient Identifier List",2:"Prior Alternate Patient ID",3:"Prior Patient Account Number",
    4:"Prior Patient ID",5:"Prior Visit Number",6:"Prior Alternate Visit ID",7:"Prior Patient Name"},
  GT1:{1:"Set ID",2:"Guarantor Number",3:"Guarantor Name",4:"Guarantor Spouse Name",
    5:"Guarantor Address",6:"Guarantor Ph Num - Home",7:"Guarantor Ph Num - Business",
    8:"Guarantor Date/Time of Birth",9:"Guarantor Administrative Sex",10:"Guarantor Type",
    11:"Guarantor Relationship",12:"Guarantor SSN"}
};

function escapeHtml(t) {
  const d = document.createElement("div");
  d.textContent = t;
  return d.innerHTML;
}

function parseHL7(raw) {
  const lines = raw.split(/\r?\n/).filter(l => l.trim());
  const fieldSep = "|";
  return lines.map((line, idx) => {
    const parts = line.split(fieldSep);
    const segType = parts[0];
    let fields;
    if (segType === "MSH") {
      fields = [
        { index: 1, value: fieldSep },
        { index: 2, value: parts[1] || "" },
        ...parts.slice(2).map((v, i) => ({ index: i + 3, value: v }))
      ];
    } else {
      fields = parts.slice(1).map((v, i) => ({ index: i + 1, value: v }));
    }
    return { segType, raw: line, fields, lineIndex: idx + 1 };
  });
}

function getFieldDesc(segType, fieldIndex) {
  const seg = FIELD_DESC[segType];
  return (seg && seg[fieldIndex]) || "";
}

function getSegDesc(segType) {
  return SEGMENT_DESC[segType] || (segType.startsWith("Z") ? "Custom Z-Segment" : "Unknown Segment");
}

function renderParsedOutput(raw) {
  const container = document.getElementById("parsedOutput");
  const segments = parseHL7(raw);

  for (const seg of segments) {
    const div = document.createElement("div");
    div.className = "parsed-segment";

    // --- Header: segment type badge + raw fields with hoverable spans ---
    let rawHtml = "";
    if (seg.segType === "MSH") {
      rawHtml += `<span class="field-span tooltip-wrap">${escapeHtml(seg.segType)}<span class="tooltip-text">${escapeHtml(seg.segType)}: ${escapeHtml(getSegDesc(seg.segType))}</span></span>`;
      for (const f of seg.fields) {
        const desc = getFieldDesc(seg.segType, f.index);
        const tip = `${seg.segType}-${f.index}` + (desc ? `: ${desc}` : "");
        if (f.index === 1) {
          rawHtml += `<span class="field-span tooltip-wrap">${escapeHtml(f.value)}<span class="tooltip-text">${escapeHtml(tip)}</span></span>`;
        } else {
          rawHtml += `<span class="sep">|</span><span class="field-span tooltip-wrap">${escapeHtml(f.value)}<span class="tooltip-text">${escapeHtml(tip)}</span></span>`;
        }
      }
    } else {
      rawHtml += `<span class="field-span tooltip-wrap">${escapeHtml(seg.segType)}<span class="tooltip-text">${escapeHtml(seg.segType)}: ${escapeHtml(getSegDesc(seg.segType))}</span></span>`;
      for (const f of seg.fields) {
        const desc = getFieldDesc(seg.segType, f.index);
        const tip = `${seg.segType}-${f.index}` + (desc ? `: ${desc}` : "");
        rawHtml += `<span class="sep">|</span><span class="field-span tooltip-wrap">${escapeHtml(f.value)}<span class="tooltip-text">${escapeHtml(tip)}</span></span>`;
      }
    }

    // --- Table body ---
    let tableRows = "";
    for (const f of seg.fields) {
      const desc = getFieldDesc(seg.segType, f.index);
      tableRows += `<tr>
        <td class="field-name">${seg.segType}-${f.index}</td>
        <td class="field-desc">${escapeHtml(desc)}</td>
        <td class="field-val">${escapeHtml(f.value)}</td>
      </tr>`;
    }

    div.innerHTML = `
      <div class="seg-header" onclick="this.parentElement.classList.toggle('expanded')">
        <span class="seg-type">${escapeHtml(seg.segType)}</span>
        <span class="seg-desc">${escapeHtml(getSegDesc(seg.segType))}</span>
        <span class="seg-raw">${rawHtml}</span>
        <span class="seg-actions">
          <button class="copy-btn" onclick="event.stopPropagation();navigator.clipboard.writeText(${JSON.stringify(seg.raw).replace(/"/g, '&quot;')})"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/><path d="M16 4h2a2 2 0 0 1 2 2v4"/><path d="M21 14H11"/><path d="m15 10-4 4 4 4"/></svg></button>
          <span class="seg-toggle"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg></span>
        </span>
      </div>
      <div class="seg-body">
        <table class="seg-table">
          <thead><tr><th style="width:15%">Segment-Field</th><th style="width:30%">Field Description</th><th style="width:55%">Field Value</th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>
    `;
    container.appendChild(div);
  }
}

// Position tooltips with fixed positioning so they never get clipped
document.addEventListener("mouseover", function(e) {
  const wrap = e.target.closest(".tooltip-wrap");
  if (!wrap) return;
  const tip = wrap.querySelector(".tooltip-text");
  if (!tip) return;
  const rect = wrap.getBoundingClientRect();
  tip.style.left = rect.left + rect.width / 2 + "px";
  tip.style.transform = "translateX(-50%)";
  if (rect.top > 40) {
    tip.style.top = "";
    tip.style.bottom = (window.innerHeight - rect.top + 4) + "px";
  } else {
    tip.style.bottom = "";
    tip.style.top = (rect.bottom + 4) + "px";
  }
});
