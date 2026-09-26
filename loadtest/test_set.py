"""The scripted utterance set for the structured latency/accuracy test.

20 short everyday sentences per language (Hindi, English, Tamil, Telugu),
plus a smaller set of intra-sentence code-switched (Tanglish-style)
sentences using Tamil mixed with English. The non-English sentences are
written by the model, not verified by a native speaker -- flagged here
and in the results report rather than presented as vetted.

Each entry is (id, source_language_or_mixed, text).
"""

ENGLISH = [
    "Hello, how are you doing today?",
    "I went to the market this morning to buy vegetables.",
    "The weather is very nice this week.",
    "Can you please send me the report by tomorrow?",
    "My brother is studying computer science in college.",
    "We watched a movie together last night.",
    "The train was delayed by twenty minutes.",
    "She works at a hospital near our house.",
    "I need to buy a new phone next month.",
    "The children are playing in the park.",
    "Please call me when you reach home.",
    "This restaurant serves very good food.",
    "He finished his homework before dinner.",
    "The meeting has been moved to three o'clock.",
    "I forgot my umbrella at the office.",
    "Our team won the match yesterday.",
    "The library closes at eight in the evening.",
    "I am planning a trip to the mountains.",
    "Her presentation was very clear and useful.",
    "The bus stop is just across the street.",
]

HINDI = [
    "नमस्ते, आज आप कैसे हैं?",
    "मैं आज सुबह सब्जियां खरीदने बाजार गया था।",
    "इस हफ्ते मौसम बहुत अच्छा है।",
    "क्या आप मुझे कल तक रिपोर्ट भेज सकते हैं?",
    "मेरा भाई कॉलेज में कंप्यूटर साइंस पढ़ रहा है।",
    "हमने कल रात साथ में एक फिल्म देखी।",
    "ट्रेन बीस मिनट देरी से आई।",
    "वह हमारे घर के पास एक अस्पताल में काम करती है।",
    "मुझे अगले महीने एक नया फोन खरीदना है।",
    "बच्चे पार्क में खेल रहे हैं।",
    "जब आप घर पहुंचें तो मुझे फोन करें।",
    "यह रेस्टोरेंट बहुत अच्छा खाना देता है।",
    "उसने खाने से पहले अपना होमवर्क पूरा किया।",
    "मीटिंग को तीन बजे कर दिया गया है।",
    "मैं अपनी छाता ऑफिस में भूल गया।",
    "हमारी टीम कल मैच जीत गई।",
    "लाइब्रेरी शाम को आठ बजे बंद होती है।",
    "मैं पहाड़ों की यात्रा की योजना बना रहा हूं।",
    "उसका प्रेजेंटेशन बहुत साफ और उपयोगी था।",
    "बस स्टॉप सड़क के उस पार ही है।",
]

TAMIL = [
    "வணக்கம், இன்று நீங்கள் எப்படி இருக்கிறீர்கள்?",
    "நான் இன்று காலை காய்கறிகள் வாங்க சந்தைக்கு சென்றேன்.",
    "இந்த வாரம் வானிலை மிகவும் நன்றாக இருக்கிறது.",
    "நீங்கள் நாளைக்குள் அறிக்கையை அனுப்ப முடியுமா?",
    "என் தம்பி கல்லூரியில் கணினி அறிவியல் படிக்கிறான்.",
    "நேற்று இரவு நாங்கள் ஒன்றாக ஒரு படம் பார்த்தோம்.",
    "ரயில் இருபது நிமிடம் தாமதமாக வந்தது.",
    "அவள் எங்கள் வீட்டிற்கு அருகில் ஒரு மருத்துவமனையில் வேலை செய்கிறாள்.",
    "அடுத்த மாதம் நான் ஒரு புதிய போன் வாங்க வேண்டும்.",
    "குழந்தைகள் பூங்காவில் விளையாடுகிறார்கள்.",
    "வீட்டிற்கு வந்ததும் என்னை அழையுங்கள்.",
    "இந்த உணவகம் மிகவும் நல்ல உணவை பரிமாறுகிறது.",
    "அவன் இரவு உணவிற்கு முன் தன் வீட்டுப்பாடத்தை முடித்தான்.",
    "கூட்டம் மூன்று மணிக்கு மாற்றப்பட்டுள்ளது.",
    "நான் என் குடையை அலுவலகத்தில் மறந்துவிட்டேன்.",
    "எங்கள் அணி நேற்று போட்டியில் வென்றது.",
    "நூலகம் மாலை எட்டு மணிக்கு மூடப்படும்.",
    "நான் மலைகளுக்கு ஒரு பயணத்தை திட்டமிடுகிறேன்.",
    "அவளுடைய விளக்கக்காட்சி மிகவும் தெளிவாகவும் பயனுள்ளதாகவும் இருந்தது.",
    "பேருந்து நிறுத்தம் தெருவுக்கு அப்பாலேயே இருக்கிறது.",
]

TELUGU = [
    "నమస్తే, ఈరోజు మీరు ఎలా ఉన్నారు?",
    "నేను ఈరోజు ఉదయం కూరగాయలు కొనడానికి మార్కెట్‌కు వెళ్ళాను.",
    "ఈ వారం వాతావరణం చాలా బాగుంది.",
    "మీరు రేపటిలోగా నివేదిక పంపగలరా?",
    "నా తమ్ముడు కళాశాలలో కంప్యూటర్ సైన్స్ చదువుతున్నాడు.",
    "నిన్న రాత్రి మేము కలిసి ఒక సినిమా చూశాము.",
    "రైలు ఇరవై నిమిషాలు ఆలస్యంగా వచ్చింది.",
    "ఆమె మా ఇంటికి దగ్గరలో ఉన్న ఆసుపత్రిలో పని చేస్తుంది.",
    "వచ్చే నెలలో నేను కొత్త ఫోన్ కొనాలి.",
    "పిల్లలు పార్కులో ఆడుకుంటున్నారు.",
    "మీరు ఇంటికి చేరుకున్నప్పుడు నాకు కాల్ చేయండి.",
    "ఈ రెస్టారెంట్ చాలా మంచి భోజనం అందిస్తుంది.",
    "అతను రాత్రి భోజనానికి ముందు తన హోంవర్క్ పూర్తి చేశాడు.",
    "సమావేశం మూడు గంటలకు మార్చబడింది.",
    "నేను నా గొడుగును ఆఫీసులో మర్చిపోయాను.",
    "మా జట్టు నిన్న మ్యాచ్ గెలిచింది.",
    "గ్రంథాలయం సాయంత్రం ఎనిమిది గంటలకు మూసివేయబడుతుంది.",
    "నేను కొండలకు ఒక ప్రయాణం ప్లాన్ చేస్తున్నాను.",
    "ఆమె ప్రజెంటేషన్ చాలా స్పష్టంగా మరియు ఉపయోగకరంగా ఉంది.",
    "బస్ స్టాప్ వీధికి అవతల వైపే ఉంది.",
]

CODEMIX_TAMIL_ENGLISH = [
    "நான் இன்னைக்கு office ku போக மாட்டேன், work from home பண்றேன்.",
    "அந்த meeting நாளைக்கு morning ku postpone ஆகிடுச்சு.",
    "நீ market போற வழியில ரொம்ப busy ஆ இருந்துச்சு.",
    "என் phone battery ரொம்ப fast ஆ drain ஆகுது.",
    "இந்த weekend நாங்க ஒரு trip plan பண்றோம்.",
    "அவன் project ரொம்ப நல்லா complete பண்ணிட்டான்.",
    "நான் இப்போ traffic la stuck ஆயிட்டேன்.",
    "அந்த restaurant food ரொம்ப tasty ஆ இருந்துச்சு.",
    "நாளைக்கு exam இருக்கு, நான் இன்னைக்கு night study பண்ணனும்.",
    "என் laptop screen crack ஆயிடுச்சு, repair பண்ணனும்.",
    "அவங்க team இந்த match ரொம்ப நல்லா play பண்ணாங்க.",
    "நான் இந்த file ஐ email ல அனுப்பிட்டேன்.",
]

ALL_STANDARD = (
    [("hi", i, text) for i, text in enumerate(HINDI)]
    + [("en", i, text) for i, text in enumerate(ENGLISH)]
    + [("ta", i, text) for i, text in enumerate(TAMIL)]
    + [("te", i, text) for i, text in enumerate(TELUGU)]
)

ALL_CODEMIX = [("mix", i, text) for i, text in enumerate(CODEMIX_TAMIL_ENGLISH)]

LANGUAGE_CODES = {"hi": "hi-IN", "en": "en-IN", "ta": "ta-IN", "te": "te-IN"}
