"""JEE exam facts and career routes. Every fact is copied from an official document, linked with its section, so
students can check it. Update each year from the new JEE (Main) Information Bulletin (jeemain.nta.nic.in) and the
JEE (Advanced) site (jeeadv.ac.in); nothing here is estimated."""

MAIN_SRC = ("JEE (Main) 2027 Information Bulletin, NTA (24 Oct 2026)",
            "https://cdnbbsr.s3waas.gov.in/s3f8e59f4b2fe7c5705bf878bbd494ccdf/uploads/2026/10/202610241890224107.pdf")
ADV_SRC = ("JEE (Advanced) 2027 eligibility criteria, IIT Delhi (13 Nov 2026)", "https://jeeadv.ac.in/eligibility.html")
ADV_HOME = ("JEE (Advanced) 2027 website", "https://jeeadv.ac.in/")

MAIN_DATES = [
    ("Session 1 application", "24 Oct – 22 Nov 2026 (late fee of ₹500 extra: 23–26 Nov 2026)"),
    ("Session 1 correction window", "27–29 Nov 2026"),
    ("Session 1 city slip / admit card", "30 Dec 2026 / 13 Jan 2027"),
    ("Session 1 exam", "20, 21, 22, 23, 24 and 28 Jan 2027"),
    ("Session 2 application", "5 Jan – 3 Feb 2027 (late fee: 4–7 Feb 2027)"),
    ("Session 2 admit card", "25 Mar 2027"),
    ("Session 2 exam", "2, 3, 4, 5, 6 and 7 Apr 2027"),
    ("Shifts", "9:00 am – 12:00 noon and 3:00 – 6:00 pm IST (3-hour papers)"),
]

MAIN_PATTERN = [
    ("Mode", "Paper 1 (B.E./B.Tech.) is a Computer Based Test.", "2.1"),
    ("Questions", "Mathematics, Physics and Chemistry: 20 MCQs (Section A) + 5 numerical-value questions (Section B) "
     "each. 75 questions, 300 marks.", "2.4"),
    ("Marking", "+4 for a correct answer, −1 for a wrong one, 0 for unanswered or marked for review without an "
     "answer. This applies to both sections.", "2.4"),
    ("Numerical answers", "Enter an integer with the on-screen keypad; round the answer to the nearest integer.", "2.4"),
    ("Duration", "3 hours (Paper 2A B.Arch and 2B B.Planning together: 3 hours 30 minutes).", "Important dates"),
    ("Languages", "13: English, Hindi, Assamese, Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, "
     "Tamil, Telugu and Urdu (English everywhere; the other language depends on the centre's state).", "2.2"),
]

MAIN_RULES = [
    ("Who can appear", "No age limit. You can appear if you passed Class XII (or equivalent) in 2025 or 2026, or are "
     "appearing in 2027. Institutes may have their own age rules.", "3.1"),
    ("Two sessions", "You need not take both. If you take both, the better of your two total NTA scores is used for "
     "the merit list.", "1.2"),
    ("Percentile (NTA score)", "Marks are converted to a percentile within your shift: the percentage of candidates "
     "in that shift who scored the same as you or lower. The shift topper gets 100. Percentiles are calculated to "
     "7 decimal places, and all shifts of both sessions are merged into one merit list.", "Normalisation annexure; 2.4"),
    ("Ties", "Equal total scores are separated by the Mathematics score, then Physics, then Chemistry, then the lower "
     "ratio of wrong to right answers (overall, then Maths, Physics, Chemistry). After that, the same rank.", "2.4 (5)"),
    ("Fee per session (India)", "General: ₹1,000 (male), ₹800 (female). Gen-EWS / OBC-NCL: ₹900 (male), ₹800 "
     "(female). SC / ST / PwD / third gender: ₹500.", "Important dates, fee table"),
]

ADV_FACTS = [
    ("Exam date", "Sunday, 23 May 2027. Organised by IIT Delhi for the 23 IITs.", ADV_HOME),
    ("Who qualifies from JEE (Main)", "The top 2,50,000 successful candidates (all categories) in the B.E./B.Tech. "
     "paper of JEE (Main) 2027: GEN-EWS 10%, OBC-NCL 27%, SC 15%, ST 7.5%, open 40.5%, with 5% for PwD within "
     "each.", ADV_SRC),
    ("Age", "Born on or after 1 October 2002 (SC, ST and PwD: on or after 1 October 1997).", ADV_SRC),
    ("Attempts", "At most two, in two consecutive years.", ADV_SRC),
    ("Class XII", "First appeared in 2026 or 2027 with Physics, Chemistry and Mathematics. Some boards that declared "
     "2024-25 results on or after 12 June 2025 are an exception: see the notice.", ADV_SRC),
    ("Earlier IIT admission", "Not eligible if already admitted to an IIT or IISc programme. Those who joined a "
     "preparatory course for the first time in 2026, and some JoSAA 2026 seat holders who did not report or "
     "withdrew in time, may appear: see the notice for the exact cases.", ADV_SRC),
]

# Routes that use JEE results, and the official places to check each.
ROUTES = [
    ("IITs", "JEE (Advanced) rank, through JoSAA.",
     "Only the top 2,50,000 in JEE (Main) Paper 1 can take JEE (Advanced).", ADV_SRC),
    ("NITs, IIITs and other centrally funded institutes", "JEE (Main) Paper 1 rank, through JoSAA (then CSAB special "
     "rounds).", "Class XII performance rules apply: see the counselling page.", ("JoSAA portal", "https://josaa.nic.in/")),
    ("State engineering colleges", "JEE (Main) Paper 1 is also used by institutions and universities funded or "
     "recognised by participating State Governments (bulletin §1.2).", "Each state runs its own counselling and "
     "publishes its own rules.", MAIN_SRC),
    ("Other programmes using JEE (Main) Paper 1", "NTA lists Integrated M.Sc., Integrated M.Tech, Integrated B.Tech, "
     "MBA, B.Sc., B.Pharm and B.Des among programmes that use Paper 1 results.", "Each institute sets its own "
     "eligibility: check its admission notice.", MAIN_SRC),
    ("Architecture (B.Arch)", "JEE (Main) Paper 2A: Mathematics and Aptitude (computer-based) plus a Drawing Test on "
     "paper.", "Taken in the same sessions as Paper 1.", MAIN_SRC),
    ("Planning (B.Planning)", "JEE (Main) Paper 2B: Mathematics, Aptitude and Planning-based questions, all "
     "computer-based.", "Taken in the same sessions as Paper 1.", MAIN_SRC),
]
