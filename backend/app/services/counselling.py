"""JoSAA / CSAB counselling guide. Every fact here is taken from the official "Business Rules for Joint Seat
Allocation ... for the academic year 2026-27" (June 01, 2026), linked below; clause numbers are given so students can
check them. Update it each year from the new Business Rules (josaa.nic.in → Business Rules)."""

SOURCE = ("JoSAA Business Rules 2026-27 (June 01, 2026)",
          "https://cdnbbsr.s3waas.gov.in/s313111c20aee51aeb480ecbd988cd8cc9/uploads/2026/06/20260601632081678.pdf")
LINKS = [("JoSAA portal", "https://josaa.nic.in/"), ("CSAB (special rounds for NIT+ seats)", "https://csab.nic.in/"),
         ("JEE (Main) — NTA", "https://jeemain.nta.nic.in/"), ("JEE (Advanced)", "https://jeeadv.ac.in/")]

STEPS = [
    ("Register and fill choices", "Register on the JoSAA portal after the JEE (Advanced) result and fill your choices "
     "of institute + program in order of preference. Two mock seat allocations show where your choices might land; "
     "choices are locked automatically when choice filling ends.", "Annexure 3"),
    ("Seat allocation rounds", "JoSAA 2026 had 5 rounds of seat allocation (13 June to 16 July 2026). In each round you "
     "get at most one seat: the highest choice your rank allows.", "Annexure 3"),
    ("Accept the seat: Freeze, Float or Slide", "Accepting the allotted seat with one of these options is mandatory to "
     "stay in the process. If you get your first choice, only Freeze is available.", "Clause 48"),
    ("Upload documents", "Upload all required documents on the JoSAA portal; they are verified only after the seat "
     "acceptance fee is paid.", "Clause 42"),
    ("Pay the seat acceptance fee", "₹15,000 for SC, ST and all PwD categories; ₹30,000 for everyone else (includes "
     "₹5,000 JoSAA processing charges). Not paying it means rejecting the seat and leaving the process. The fee, minus "
     "the ₹5,000 charges, is adjusted against the admission fee.", "Clause 42, Step 3"),
    ("NIT+ seats: partial admission fee", "For NITs, IIITs and other GFTIs (the NIT+ System) a partial admission fee is "
     "also due: ₹20,000 for SC, ST and all PwD categories, ₹45,000 for others. It is not refundable; not paying it "
     "forfeits the seat, which then goes to CSAB special rounds.", "Clauses 59–62"),
    ("Report to the institute", "After the final round, report to the admitting institute as it instructs.", "Section XXI"),
]

OPTIONS = [
    ("Freeze", "You accept this seat and stop. You will not be considered in later rounds; the seat stays yours."),
    ("Float", "You accept this seat, and in later rounds you'll move up to any higher choice in any institute if it "
              "opens. Otherwise you keep this seat."),
    ("Slide", "You accept this seat, and in later rounds you'll move up only to a higher choice in the same institute."),
]

ELIGIBILITY = [
    "IITs/IISc: a JEE (Advanced) rank, with Physics, Chemistry and Mathematics in Class XII (Annexure 2(a)).",
    "NITs, IIITs and other GFTIs (NIT+ System): a JEE (Main) rank, and Class XII appeared for the first time in "
    "2024, 2025 or 2026 (Annexure 2(b)).",
    "Class XII performance (both): at least 75% aggregate (65% for SC, ST and PwD), OR be in the category-wise top 20 "
    "percentile of successful candidates of your board.",
]

QUOTAS = [
    ("All India (AI)", "IITs, IIITs and most GFTIs: one merit list for everyone."),
    ("Home State (HS)", "NITs, IIEST Shibpur and PEC Chandigarh: only for candidates whose Home State/UT (by the State "
                        "Code of Eligibility) is where the institute is."),
    ("Other State (OS)", "The same institutes' seats for candidates from every other State/UT."),
    ("Special cases", "GKCIET Malda's Home State quota covers the eight north-eastern states; some institutes use "
                      "Home State + All India instead (Section V)."),
]

WITHDRAWAL = [
    "IIT/IISc seats: you can withdraw from round 2 up to the second-last round (2026: until the round-4 window closed "
    "on 14 July, 17:00 IST); not after that.",
    "NIT+ seats: you can withdraw up to the final round of online seat allocation.",
    "Withdrawing cancels your seat and ends your JoSAA participation. The seat acceptance fee is refunded after "
    "deducting ₹7,000 withdrawal processing charges.",
]

SCHEDULE_2026 = [("2 Jun", "Registration and choice filling opens"), ("8 & 10 Jun", "Mock seat allocations 1 and 2"),
                 ("11 Jun", "Choice filling ends (choices auto-locked)"), ("13 Jun", "Round 1"),
                 ("30 Jun", "Round 2"), ("6 Jul", "Round 3"), ("10 Jul", "Round 4"), ("16 Jul", "Round 5")]

TIPS = [
    "Order choices by where you would rather study, not by your chances; the algorithm gives you the highest choice "
    "your rank allows, so a dream choice above a safe one costs nothing.",
    "Use the mock allocations to sanity-check your list, then add enough safer choices at the end.",
    "Check each program's past closing ranks in your own quota, seat type and gender pool (the Colleges page and the "
    "Predictor do this from JoSAA's own tables).",
    "Keep every document ready before choice filling ends, in the format the Business Rules ask for.",
]
