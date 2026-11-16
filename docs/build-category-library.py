# Generates docs/category-library.js. Run from the repo root: python3 docs/build-category-library.py 1000
import json
ICONS={
"people":'<path d="M16 19v-1a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v1"/><circle cx="10" cy="7" r="3.5"/><path d="M20 19v-1a4 4 0 0 0-3-3.9M15.5 3.6a3.5 3.5 0 0 1 0 6.8"/>',
"person":'<circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/>',
"heart":'<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
"baby":'<circle cx="12" cy="12" r="8"/><path d="M9 11h.01M15 11h.01M9.5 15a3.5 3.5 0 0 0 5 0M12 4c-1 1.5-.5 3 1 3"/>',
"paw":'<circle cx="6.5" cy="10.5" r="1.8"/><circle cx="17.5" cy="10.5" r="1.8"/><circle cx="9.5" cy="6" r="1.8"/><circle cx="14.5" cy="6" r="1.8"/><path d="M12 12c-3 0-5 3.5-5 5.5 0 1.5 1.5 2 2.5 2s1.5-.5 2.5-.5 1.5.5 2.5.5 2.5-.5 2.5-2c0-2-2-5.5-5-5.5z"/>',
"places":'<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
"events":'<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M8 3v4M16 3v4M3.5 10h17"/><path d="m12 12.3 1.1 2.2 2.4.3-1.8 1.7.5 2.4-2.2-1.2-2.2 1.2.5-2.4-1.8-1.7 2.4-.3z"/>',
"things":'<path d="M12 3 3.5 7.5v9L12 21l8.5-4.5v-9z"/><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9"/>',
"year":'<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M8 3v4M16 3v4M3.5 10h17M8 14h2M14 14h2M8 17h2"/>',
"tag":'<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1 1 0 0 1 0 1.4l-7.2 7.2a1 1 0 0 1-1.4 0z"/><circle cx="8" cy="8" r="1.5"/>',
"tree":'<path d="M12 3 6 11h3l-4 6h14l-4-6h3z"/><path d="M12 17v4"/>',
"mountain":'<path d="m3 20 6.5-11 4 6.5 2.5-3.5L21 20z"/>',
"water":'<path d="M3 9c2 0 2-1.5 4.5-1.5S10 9 12 9s2-1.5 4.5-1.5S19 9 21 9M3 14c2 0 2-1.5 4.5-1.5S10 14 12 14s2-1.5 4.5-1.5S19 14 21 14M3 19c2 0 2-1.5 4.5-1.5S10 19 12 19s2-1.5 4.5-1.5S19 19 21 19"/>',
"sun":'<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
"snow":'<path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9M9.5 4.5 12 7l2.5-2.5M9.5 19.5 12 17l2.5 2.5"/>',
"leaf":'<path d="M5 19c0-8 5-14 15-14 0 10-6 15-14 15"/><path d="M5 19c3-4 6-7 10-9"/>',
"flower":'<circle cx="12" cy="8" r="2"/><circle cx="8.5" cy="6" r="2"/><circle cx="15.5" cy="6" r="2"/><circle cx="10" cy="10.5" r="2"/><circle cx="14" cy="10.5" r="2"/><path d="M12 12.5V21M12 18c-2 0-3.5-1-4-3M12 19c2 0 3.5-1 4-3"/>',
"sprout":'<path d="M12 21v-9M12 12c0-4-3-6-7-6 0 4 3 6 7 6zM12 14c0-3.5 2.5-6 6.5-6 0 4-2.5 6-6.5 6z"/>',
"cloud":'<path d="M7 18.5a4.5 4.5 0 0 1-.5-9 6 6 0 0 1 11.5 1.5 3.8 3.8 0 0 1-.5 7.5z"/>',
"moon":'<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
"food":'<path d="M7 3v8a2 2 0 0 0 4 0V3M9 11v10M17 21V3c-2 1-3 4-3 8h3"/>',
"coffee":'<path d="M4 9h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M17 10.5h1.5a2.5 2.5 0 0 1 0 5H17M8 3.5v2.5M12 3.5v2.5"/>',
"drink":'<path d="M7 3.5h10c0 5-2 8.5-5 8.5S7 8.5 7 3.5zM12 12v8M8.5 20.5h7"/>',
"cake":'<path d="M4 21h16v-8a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2z"/><path d="M4 16c2 0 2 1.5 4 1.5s2-1.5 4-1.5 2 1.5 4 1.5 2-1.5 4-1.5M12 11V7M12 4.5v-.5"/>',
"gift":'<rect x="3.5" y="8" width="17" height="4" rx="1"/><path d="M5 12v8h14v-8M12 8v12M12 8c-1.5-3-5-3.5-5-1.5S10 8 12 8zm0 0c1.5-3 5-3.5 5-1.5S14 8 12 8z"/>',
"balloon":'<path d="M12 3.5c-3.3 0-5.5 2.7-5.5 6 0 3.5 3 6.5 5.5 6.5s5.5-3 5.5-6.5c0-3.3-2.2-6-5.5-6z"/><path d="M12 16l-1 2h2l-1-2M12 18c0 1.5-1.5 2-1.5 3.5"/>',
"ring":'<circle cx="12" cy="14.5" r="6"/><path d="m9.5 4 2.5 4 2.5-4-1-1.5h-3z"/>',
"sparkle":'<path d="M12 3c.7 4.3 2.7 6.3 7 7-4.3.7-6.3 2.7-7 7-.7-4.3-2.7-6.3-7-7 4.3-.7 6.3-2.7 7-7zM18.5 15.5c.3 1.6 1 2.3 2.5 2.5-1.5.3-2.2 1-2.5 2.5-.3-1.5-1-2.2-2.5-2.5 1.5-.2 2.2-.9 2.5-2.5z"/>',
"plane":'<path d="M2.5 12.5 21 5.5l-4 14-5-5.5z"/><path d="m12 14-2 4.5v-6"/>',
"car":'<path d="M5 11l2-4.5h10L19 11"/><rect x="3.5" y="11" width="17" height="5.5" rx="1.5"/><circle cx="7.5" cy="18" r="1.5"/><circle cx="16.5" cy="18" r="1.5"/>',
"train":'<rect x="5" y="3.5" width="14" height="13" rx="3"/><path d="M5 10.5h14M8.5 20.5 10 16.5M15.5 20.5 14 16.5M8.5 13.5h.01M15.5 13.5h.01"/>',
"boat":'<path d="M3 16h18l-2.5 4h-13z"/><path d="M12 3v13M12 4l6 9h-6"/>',
"bike":'<circle cx="6" cy="16" r="3.5"/><circle cx="18" cy="16" r="3.5"/><path d="M6 16l4-7h5l3 7M10 9l2 7M14 6h2.5"/>',
"map":'<path d="M3.5 6.5 9 4l6 2.5L20.5 4v13.5L15 20l-6-2.5L3.5 20z"/><path d="M9 4v13.5M15 6.5V20"/>',
"flag":'<path d="M5 21V4M5 4.5c4-2 7 2 14 0v9c-7 2-10-2-14 0"/>',
"tent":'<path d="M3 20 12 4l9 16z"/><path d="M9.5 20 12 14l2.5 6"/>',
"home":'<path d="M3.5 11 12 4l8.5 7"/><path d="M5.5 9.5V20h13V9.5M10 20v-5h4v5"/>',
"city":'<path d="M3.5 21V9l5-3v15M8.5 21V4h7v17M15.5 21v-9h5v9M2.5 21h19"/><path d="M11 8h2M11 11h2M11 14h2"/>',
"farm":'<path d="M3.5 21V10L12 4l8.5 6v11z"/><path d="M8.5 21v-6h7v6M8.5 15l7 6M15.5 15l-7 6"/>',
"faith":'<path d="M12 2.5v4M10 4.5h4M5 21v-8l7-5 7 5v8z"/><path d="M10 21v-4a2 2 0 0 1 4 0v4"/>',
"work":'<rect x="3.5" y="7.5" width="17" height="12" rx="2"/><path d="M9 7.5V5.5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M3.5 12.5h17"/>',
"school":'<path d="M2.5 9.5 12 5l9.5 4.5L12 14z"/><path d="M6.5 11.5V16c3 2 8 2 11 0v-4.5M21.5 9.5V15"/>',
"book":'<path d="M4 5.5A2 2 0 0 1 6 3.5h13v14H6a2 2 0 0 0-2 2z"/><path d="M4 19.5a2 2 0 0 0 2 2h13v-4"/>',
"document":'<path d="M6 3.5h8l4 4V20.5H6z"/><path d="M14 3.5v4h4M9 12h6M9 15.5h6"/>',
"ball":'<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.5 2.5 14.5 0 17M12 3.5c-2.5 2.5-2.5 14.5 0 17"/>',
"trophy":'<path d="M7.5 4h9v5a4.5 4.5 0 0 1-9 0z"/><path d="M7.5 6H4.5c0 3 1.5 4.5 3.3 4.7M16.5 6h3c0 3-1.5 4.5-3.3 4.7M12 13.5V17M8.5 20.5h7l-.5-3.5h-6z"/>',
"fitness":'<path d="M6.5 6.5v11M17.5 6.5v11M3.5 9v6M20.5 9v6M6.5 12h11"/>',
"music":'<path d="M9 18V5.5l11-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
"film":'<rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M7.5 4v16M16.5 4v16M3.5 8h4M3.5 12h4M3.5 16h4M16.5 8h4M16.5 12h4M16.5 16h4"/>',
"game":'<rect x="2.5" y="7.5" width="19" height="10" rx="5"/><path d="M7.5 10.5v4M5.5 12.5h4M15 11.5h.01M17.5 13.5h.01"/>',
"art":'<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-1.2-1-1.5-1-2.6 0-1 .8-1.7 1.8-1.7h2.2a3.7 3.7 0 0 0 3.7-3.7c0-4.3-3.8-7.3-8.5-7.3z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7.5" r="1"/><circle cx="14.5" cy="7.5" r="1"/>',
"camera":'<path d="M4 8h3l1.5-2.5h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.5"/>',
"shop":'<path d="M5 8h14l-1 12.5H6z"/><path d="M9 10V7a3 3 0 0 1 6 0v3"/>',
"shirt":'<path d="M8.5 3.5 4 6l1.5 4 2-1V20h9V9l2 1L20 6l-4.5-2.5c-.5 1.5-2 2.5-3.5 2.5s-3-1-3.5-2.5z"/>',
"tools":'<path d="M14.7 6.3a4 4 0 0 1 5 5l-2.2-.6-2-2-.6-2.2zM15.5 8.5 5 19a1.4 1.4 0 0 1-2-2L13.5 6.5"/>',
"health":'<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="M12 8v8M8 12h8"/>',
"star":'<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
"clock":'<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
"smile":'<circle cx="12" cy="12" r="8.5"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0M9 10h.01M15 10h.01"/>',
"screen":'<rect x="3" y="4.5" width="18" height="12" rx="1.5"/><path d="M8.5 20h7M12 16.5V20"/>',
"phone":'<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
"archive":'<rect x="3.5" y="4" width="17" height="4.5" rx="1"/><path d="M5 8.5V20h14V8.5M10 12h4"/>',
"drop":'<path d="M12 3.5s-6 6.5-6 10.5a6 6 0 0 0 12 0c0-4-6-10.5-6-10.5z"/>',
"fish":'<path d="M3 12c3-4.5 9-6 14-2l4-3v10l-4-3c-5 4-11 2.5-14-2z"/><circle cx="15" cy="11" r=".8"/>',
}
L={
"people":"People|Family|Friends|Extended family|Relatives|Coworkers|Colleagues|Neighbors|Classmates|Teammates|Roommates|Guests|Crowds|Groups|Couples|Strangers|Team|Club members|Volunteers|Community|Family reunions|Ancestors|Family tree|Generations|Siblings|Cousins|In-laws|Grandparents|Grandchildren|Godparents|Bandmates|Mentors|Students|Teachers|Clients",
"person":"Portraits|Selfies|Headshots|Me|Profile pictures|Candids|Faces|Mom|Dad|Grandma|Grandpa|Brother|Sister|Aunt|Uncle|Husband|Wife|Partner|Best friend|Son|Daughter|Twins|Models|Characters|Celebrities|Self portraits|Close-ups|Poses|Expressions|Hairstyles",
"heart":"Love|Romance|Dates|Date night|Anniversaries|Valentine's Day|Honeymoon|Engagement|Proposals|Favorites|Memories|Sweet moments|Hugs|Kisses|Friendship|Kindness|Charity|Mother's Day|Father's Day|Grandparents Day|Family time|Cuddles|Love notes|First dates|Sentimental",
"baby":"Kids|Children|Babies|Newborn|Toddlers|Teens|Baby's first year|Milestones|First steps|First words|Pregnancy|Baby shower|Gender reveal|Playtime|Playground|Daycare|Preschool|Kids' art|Bath time|Nap time|Growing up|Monthly photos|Grandkids|Nursery|Siblings together",
"paw":"Pets|Animals|Dogs|Cats|Puppies|Kittens|Horses|Birds|Rabbits|Hamsters|Reptiles|Wildlife|Zoo|Aquarium animals|Farm animals|Bugs|Butterflies|Pet adoption|Vet visits|Dog park|Pet birthdays|Pet costumes|Safari|Bird watching|Petting zoo",
"places":"Places|Locations|Landmarks|Neighborhood|Hometown|Local spots|Parks|Restaurants visited|Cafes|Venues|Museums|Monuments|Attractions|Hotels|Airbnbs|Viewpoints|Bridges|Castles|Ruins|Lighthouses",
"events":"Events|Occasions|Holidays|Celebrations|Festivals|Fairs|Parades|Ceremonies|Gatherings|Reunions|Open house|Conferences|Meetups|Fundraisers|Galas|Award nights|Premieres|Expos|Conventions|Shows",
"things":"Things|Objects|Stuff|Collections|Collectibles|Antiques|Heirlooms|Keepsakes|Gadgets|Toys|Furniture|Decor|Household items|Possessions|Inventory|Items for sale|Insurance photos|Before and after|Product photos|Packaging",
"year":"Years|Months|Seasons|Calendar|Dates taken|Decades|This year|Last year|Throwbacks|On this day|Weekends|Weekdays|School year|Fiscal year|Countdown",
"tree":"Nature|Forests|Trees|Woods|Jungle|National parks|Trails|Outdoors|Scenery|Landscapes|Countryside|Meadows|Wilderness|Rainforest|Botanical gardens",
"mountain":"Mountains|Hiking|Climbing|Rock climbing|Peaks|Canyons|Cliffs|Volcanoes|Hills|Valleys|Deserts|Caves|Summits|Trekking|Backpacking",
"water":"Beach|Ocean|Sea|Lakes|Rivers|Waterfalls|Swimming|Pool|Surfing|Snorkeling|Scuba diving|Kayaking|Canoeing|Rafting|Water park|Coast|Islands|Harbors|Sailing|Paddleboarding",
"sun":"Summer|Sunshine|Sunsets|Sunrises|Golden hour|Vacation|Spring break|Summer camp|Heat wave|Tropical|Picnics|Barbecues|Pool parties|Fourth of July|Labor Day|Memorial Day|Beach days",
"snow":"Winter|Snow|Skiing|Snowboarding|Ice skating|Sledding|Snowmen|Hockey|Winter holidays|Christmas|Hanukkah|New Year's Eve|New Year's Day|Ski trips|Snow days|Ice",
"leaf":"Fall|Autumn|Fall foliage|Harvest|Thanksgiving|Halloween|Pumpkin patch|Apple picking|Hayrides|Corn maze|Back to school|Leaves",
"flower":"Flowers|Spring|Blossoms|Bouquets|Gardens|Wildflowers|Roses|Tulips|Cherry blossoms|Easter|Floral|Garden tours|Plants",
"sprout":"Gardening|Vegetable garden|Houseplants|Growth|Seedlings|Landscaping|Yard work|Lawn|Composting|Greenhouse",
"cloud":"Weather|Storms|Rain|Clouds|Fog|Rainbows|Lightning|Sky|Skies|Tornadoes|Hurricanes|Wind",
"moon":"Night|Night sky|Stars|Astronomy|Moon|Eclipses|Northern lights|City lights|Nightlife|Fireworks|Bonfires|Campfires|Late nights",
"food":"Food|Meals|Dinners|Lunches|Breakfast|Brunch|Recipes|Cooking|Baking|Home cooking|Restaurants|Dining out|Takeout|Desserts|Snacks|Food trucks|Street food|Groceries|Farmers market|Family recipes|Holiday dinners|Potlucks|Meal prep|Pizza|Sushi",
"coffee":"Coffee|Tea|Coffee shops|Cafe visits|Morning routine|Tea parties",
"drink":"Drinks|Cocktails|Wine|Beer|Wine tasting|Breweries|Wineries|Bars|Happy hour|Toasts|Parties|Nights out",
"cake":"Birthdays|Birthday parties|Cakes|Sweet sixteen|Quinceañera|Bar mitzvah|Bat mitzvah|First birthday|Milestone birthdays|Surprise parties|Retirement party",
"gift":"Gifts|Presents|Christmas morning|Holiday gifts|Secret Santa|Wish list|Unboxing|Stocking stuffers|Gift ideas",
"balloon":"Party|Celebration|Graduation party|Housewarming|Engagement party|Going away party|Welcome home|Bachelor party|Bachelorette party|Block party|Carnival|Circus|Theme parks|Amusement parks",
"ring":"Weddings|Wedding|Bridal shower|Rehearsal dinner|Vow renewal|Wedding party|Ceremony|Reception|Wedding dress|Rings|Jewelry",
"sparkle":"Highlights|Best of|Special moments|Magic moments|Firsts|Achievements|Big moments|Life events|Bucket list|Dreams|Inspiration|Wow|Hidden gems|Moments",
"plane":"Travel|Trips|Vacations|Flights|Airports|Abroad|Road trip|Weekend getaways|Business trips|Study abroad|Honeymoon trip|Cruise|Europe|Asia|Africa|South America|Australia|Mexico|Caribbean|Hawaii|Disney|Paris|London|New York|Japan|Italy",
"car":"Cars|Road trips|Driving|Car shows|Classic cars|Motorcycles|Trucks|First car|Racing|Commute|Car camping|RV trips",
"train":"Trains|Public transit|Subway|Bus rides|Train trips|Rail journeys",
"boat":"Boats|Boating|Cruises|Ferries|Yachts|Marina|Fishing trips",
"bike":"Biking|Cycling|Mountain biking|Bike rides|Bike trips|Scooters|Skateboarding",
"map":"Maps|Places visited|Countries visited|States visited|Cities visited|Road atlas|Itineraries|Routes|Geocaching|Exploring",
"flag":"Countries|Patriotic|Military|Veterans|Independence Day|National holidays|Flags|Cultural events|Heritage|Embassy",
"tent":"Camping|Glamping|Campgrounds|Scouts|Cabins|Outdoor adventures|Survival trips",
"home":"Home|House|Our home|Apartment|Moving|New home|Home renovation|Remodel|Interior design|Rooms|Kitchen|Backyard|Holidays at home|Home life|Childhood home|Housework|Decorating",
"city":"City|Cities|Downtown|Architecture|Buildings|Skylines|Streets|Urban|Street photography|Neighborhoods|Skyscrapers|Bridges and tunnels|Graffiti|Murals|Shops and storefronts",
"farm":"Farm|Farms|Ranch|Barn|Rural life|Country living|Tractors|Orchards|Vineyards|County fair",
"faith":"Church|Faith|Religion|Baptism|Christening|First communion|Confirmation|Mass|Temple|Mosque|Synagogue|Diwali|Eid|Passover|Ramadan|Religious holidays|Prayer|Funeral|Memorials|In memoriam",
"work":"Work|Office|Business|Career|Job|Work events|Team building|Office party|Conferences attended|Projects|Clients and customers|Coworking|Side hustle|Small business|Workplace|Promotions|Retirement|Networking",
"school":"School|Graduation|College|University|High school|Middle school|Elementary school|Kindergarten|Class photos|School events|Prom|Homecoming|Yearbook|Field trips|Science fair|Report cards|Campus|Dorm life|Alumni|Reunion",
"book":"Books|Reading|Library|Journals|Scrapbook|Stories|Poetry|Book club|Studying|Notes|Homework|Learning|Education|Comics",
"document":"Documents|Receipts|Bills|Paperwork|Forms|Contracts|Taxes|Records|Warranties|Manuals|IDs|Passports|Certificates|Medical records|Tickets|Invoices|Letters|Cards|Whiteboards|Handwriting|Recipes cards|Notes and lists",
"ball":"Sports|Soccer|Football|Baseball|Basketball|Softball|Volleyball|Tennis|Golf|Pickleball|Rugby|Cricket|Lacrosse|Bowling|Games day|Little league|Tailgating|Stadiums|Sporting events|Fantasy league",
"trophy":"Awards|Trophies|Competitions|Tournaments|Championships|Medals|Wins|Recitals|Contests|Spelling bee|Honor roll|Recognition",
"fitness":"Fitness|Gym|Workouts|Running|Marathons|5K races|Yoga|Pilates|CrossFit|Martial arts|Boxing|Dance|Gymnastics|Cheerleading|Wrestling|Swim meets|Triathlon|Weight loss|Progress photos",
"music":"Music|Concerts|Live music|Band|Choir|Instruments|Piano|Guitar|Music lessons|Music festivals|Karaoke|Recitals and performances|DJ|Singing",
"film":"Movies|Film|Theater|Plays|Musicals|Broadway|Cinema|Movie night|Videos|Home movies|Behind the scenes|Film sets|Drama club|Comedy shows",
"game":"Games|Video games|Board games|Card games|Game night|Puzzles|Gaming|Arcade|Esports|Cosplay|Tabletop|Chess",
"art":"Art|Crafts|DIY crafts|Painting|Drawing|Sketches|Pottery|Sculpture|Knitting|Sewing|Quilting|Woodworking|Art projects|Galleries|Art shows|Calligraphy|Design|Tattoos|Makeup|Nails",
"camera":"Photography|Photo shoots|Photoshoots|Family photos|Professional photos|Film photography|Black and white|Edited|Favorites to print|Prints|Wallpapers|Album covers|Photo booth|Drone photos|Panoramas",
"shop":"Shopping|Purchases|Stores|Malls|Online orders|Thrift finds|Yard sales|Flea markets|Black Friday|Deals|Outfit ideas",
"shirt":"Fashion|Outfits|Clothes|Style|Costumes|Halloween costumes|Uniforms|Shoes|Accessories|Hats|Dresses|Suits|Closet|Hand-me-downs",
"tools":"DIY|Projects|Repairs|Home improvement|Building|Construction|Renovation|Tools|Workshop|Fixes|Maintenance|Car repairs|Before and after projects",
"health":"Health|Medical|Doctor visits|Hospital|Dental|Surgery|Recovery|Medications|Wellness|Self care|Therapy|Injuries|Checkups|Birth",
"star":"Favorites list|Starred|Top picks|Best photos|Keepers|Portfolio|Featured|Must see|Hall of fame|Fan favorites",
"clock":"Morning|Afternoon|Evening|Daily life|Everyday|Routines|Time lapse|History|Then and now|Old times",
"smile":"Funny|Memes|Silly|Happy|Laughter|Goofy faces|Inside jokes|Pranks|Bloopers|Mood|Emotions|Feelings|Good vibes",
"screen":"Screenshots|Screen captures|Computer|Websites|Apps|Social media|Online|Video calls|Zoom calls|Streaming|Wallpapers and backgrounds",
"phone":"Phone|Texts|Messages|Downloads|WhatsApp|Instagram|Facebook|Shared with me|From friends|Saved images",
"archive":"Archive|Old photos|Vintage|Scanned photos|Slides|Negatives|Family archive|Historical|Old family photos|Polaroids|To sort|Unsorted|Misc|Miscellaneous|Backups|Duplicates|Junk|To delete",
"drop":"Colors|Black|White|Red|Blue|Green|Yellow|Pastels|Neon|Monochrome|Abstract|Patterns|Textures|Minimalist",
"fish":"Fishing|Fish|Ice fishing|Fly fishing|Catches|Aquarium|Marine life|Whale watching|Seafood",
}
EXTRA={
"people":"Book club members|Church group|Playgroup|Support group|Travel buddies|Hiking buddies|Gym buddies|Pen pals",
"person":"Groom|Bride|Officiant|Baby pictures of me|Graduates|Photographer",
"paw":"Fish tank|Guinea pigs|Chickens|Ducks|Cows|Goats|Turtles|Snakes|Parrots|Ferrets",
"food":"Tacos|Burgers|Pasta|Ice cream|Chocolate|Cookies|Bread|Soup|Salads|Vegan",
"places":"Libraries|Train stations|Gas stations|Malls visited|Stadium visits|Waterfront",
"events":"Book signings|Workshops|Seminars|Retreats|Webinars|Auctions",
"ball":"Hockey games|Ultimate frisbee|Badminton|Table tennis|Dodgeball|Kickball",
"music":"Opera|Orchestra|Jazz|Drums|Violin|Marching band",
"school":"Graduation photos|Teacher appreciation|Tutoring|Exams",
}
for k,v in EXTRA.items(): L[k]+="|"+v
seen=set();lib=[]
for ic,names in L.items():
  assert ic in ICONS,ic
  for n in names.split("|"):
    k=n.lower()
    if k in seen: continue
    seen.add(k);lib.append([n,ic])
print(len(lib))
import sys
N=int(sys.argv[1]) if len(sys.argv)>1 else 0
if N:
  out="docs/category-library.js"
  # Round-robin across icon groups so every group keeps its most common names.
  groups={}
  for n,ic in lib: groups.setdefault(ic,[]).append([n,ic])
  MUST="Birthdays|Vacations|Holidays|Kids|Dogs|Cats|Concerts|Weddings|Thanksgiving|Easter|Christmas|Hiking|Work|School|Receipts|Documents|Friends|Mom|Dad|Babies|Gardening|Recipes|Cars|Home|Art|Music|Movies|Games|Fitness|Church|Funeral|Party|Grandparents|Anniversaries|Road trip|Sunsets|Snow|Fall|Summer|Winter|Spring|Flowers|Nature|Mountains|Ocean|Lakes|Pool|Selfies|Portraits|Family photos|Vintage|Scanned photos|Memes|Funny|Coworkers|Neighbors|Restaurants|Coffee|Wine|Parties|Graduation|Prom|Camping|Fishing|Boats|Biking|Running|Yoga|Soccer|Baseball|Basketball|Football|Golf|Crafts|DIY|Shopping|Fashion|Outfits|Costumes|Health|Hospital|Baby shower|Pregnancy|Disney|Cruise|Europe|Zoo|Wildlife|Horses|Birds|Farm|City|Architecture|Night|Fireworks|Weather|Screenshots|Texts|Junk|Duplicates|Halloween|New Year's Eve|Hanukkah|Fourth of July|Valentine's Day|Mother's Day|Father's Day|Diwali|Eid|Passover|Family|Pets|Travel|Beach|Food|Sports|Old photos".split("|")
  byname={n:[n,ic] for n,ic in lib}
  picked=[byname[m] for m in MUST if m in byname];i=0
  while len(picked)<N:
    added=False
    for ic in groups:
      if i<len(groups[ic]) and len(picked)<N:
        added=True
        if groups[ic][i] not in picked: picked.append(groups[ic][i])
    if not added: break
    i+=1
  lib=picked  # most common first: priority names, then round-robin by group
  js="/* Built-in category library: "+str(len(lib))+" names people commonly use, each with an icon.\n   Hidden from the user. Used to suggest names while typing a new category and to auto-assign its icon. */\n"
  js+="window.CATEGORY_ICONS="+json.dumps(ICONS,ensure_ascii=False,indent=0)+";\n"
  js+="window.CATEGORY_LIBRARY="+json.dumps(lib,ensure_ascii=False,separators=(",",":"))+";\n"
  open(out,"w").write(js);print("wrote",len(lib))
