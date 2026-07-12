import type { RegionsCatalog } from './regions.js';

// AWS Lightsail's region index only publishes region codes, so this static map
// supplies a friendly name for each. New AWS regions are rare, so it drifts
// slowly; an unmapped live code is still surfaced (see regions.ts) rather than
// dropped. Display strings are ASCII, "CC - City".
export const AWS_REGION_CITIES: Record<string, { city: string; country: string }> = {
  'us-east-1': { city: 'Virginia', country: 'US' },
  'us-east-2': { city: 'Ohio', country: 'US' },
  'us-west-2': { city: 'Oregon', country: 'US' },
  'ca-central-1': { city: 'Montreal', country: 'CA' },
  'eu-central-1': { city: 'Frankfurt', country: 'DE' },
  'eu-west-1': { city: 'Ireland', country: 'IE' },
  'eu-west-2': { city: 'London', country: 'GB' },
  'eu-west-3': { city: 'Paris', country: 'FR' },
  'eu-north-1': { city: 'Stockholm', country: 'SE' },
  'eu-south-2': { city: 'Zaragoza', country: 'ES' },
  'ap-south-1': { city: 'Mumbai', country: 'IN' },
  'ap-northeast-1': { city: 'Tokyo', country: 'JP' },
  'ap-northeast-2': { city: 'Seoul', country: 'KR' },
  'ap-east-1': { city: 'Hong Kong', country: 'HK' },
  'ap-southeast-1': { city: 'Singapore', country: 'SG' },
  'ap-southeast-2': { city: 'Sydney', country: 'AU' },
  'ap-southeast-3': { city: 'Jakarta', country: 'ID' },
  'ap-southeast-5': { city: 'Malaysia', country: 'MY' },
  'sa-east-1': { city: 'Sao Paulo', country: 'BR' }, // AWS-preferred over Vultr's sao
};

// Baked snapshot served when a cold-cache catalog fetch fails (so GET /regions
// and POST /deploy still work before the first successful provider fetch). A
// frozen point-in-time copy of the generated catalog; the live generator
// supersedes it as soon as a fetch succeeds.
export const BAKED_CATALOG: RegionsCatalog = {
  amsterdam: { display: 'NL - Amsterdam', provider: 'vultr', region: 'ams' },
  atlanta: { display: 'US - Atlanta', provider: 'vultr', region: 'atl' },
  bangalore: { display: 'IN - Bangalore', provider: 'vultr', region: 'blr' },
  chicago: { display: 'US - Chicago', provider: 'vultr', region: 'ord' },
  dallas: { display: 'US - Dallas', provider: 'vultr', region: 'dfw' },
  'delhi-ncr': { display: 'IN - Delhi NCR', provider: 'vultr', region: 'del' },
  frankfurt: { display: 'DE - Frankfurt', provider: 'aws', region: 'eu-central-1' },
  honolulu: { display: 'US - Honolulu', provider: 'vultr', region: 'hnl' },
  ireland: { display: 'IE - Ireland', provider: 'aws', region: 'eu-west-1' },
  jakarta: { display: 'ID - Jakarta', provider: 'aws', region: 'ap-southeast-3' },
  johannesburg: { display: 'ZA - Johannesburg', provider: 'vultr', region: 'jnb' },
  london: { display: 'GB - London', provider: 'aws', region: 'eu-west-2' },
  'los-angeles': { display: 'US - Los Angeles', provider: 'vultr', region: 'lax' },
  madrid: { display: 'ES - Madrid', provider: 'vultr', region: 'mad' },
  malaysia: { display: 'MY - Malaysia', provider: 'aws', region: 'ap-southeast-5' },
  manchester: { display: 'GB - Manchester', provider: 'vultr', region: 'man' },
  melbourne: { display: 'AU - Melbourne', provider: 'vultr', region: 'mel' },
  'mexico-city': { display: 'MX - Mexico City', provider: 'vultr', region: 'mex' },
  miami: { display: 'US - Miami', provider: 'vultr', region: 'mia' },
  milan: { display: 'IT - Milan', provider: 'vultr', region: 'mxp' },
  montreal: { display: 'CA - Montreal', provider: 'aws', region: 'ca-central-1' },
  mumbai: { display: 'IN - Mumbai', provider: 'aws', region: 'ap-south-1' },
  'new-jersey': { display: 'US - New Jersey', provider: 'vultr', region: 'ewr' },
  ohio: { display: 'US - Ohio', provider: 'aws', region: 'us-east-2' },
  oregon: { display: 'US - Oregon', provider: 'aws', region: 'us-west-2' },
  osaka: { display: 'JP - Osaka', provider: 'vultr', region: 'itm' },
  paris: { display: 'FR - Paris', provider: 'aws', region: 'eu-west-3' },
  santiago: { display: 'CL - Santiago', provider: 'vultr', region: 'scl' },
  'sao-paulo': { display: 'BR - Sao Paulo', provider: 'vultr', region: 'sao' },
  seattle: { display: 'US - Seattle', provider: 'vultr', region: 'sea' },
  seoul: { display: 'KR - Seoul', provider: 'aws', region: 'ap-northeast-2' },
  'silicon-valley': { display: 'US - Silicon Valley', provider: 'vultr', region: 'sjc' },
  singapore: { display: 'SG - Singapore', provider: 'aws', region: 'ap-southeast-1' },
  stockholm: { display: 'SE - Stockholm', provider: 'aws', region: 'eu-north-1' },
  sydney: { display: 'AU - Sydney', provider: 'aws', region: 'ap-southeast-2' },
  'tel-aviv': { display: 'IL - Tel Aviv', provider: 'vultr', region: 'tlv' },
  tokyo: { display: 'JP - Tokyo', provider: 'aws', region: 'ap-northeast-1' },
  toronto: { display: 'CA - Toronto', provider: 'vultr', region: 'yto' },
  virginia: { display: 'US - Virginia', provider: 'aws', region: 'us-east-1' },
  warsaw: { display: 'PL - Warsaw', provider: 'vultr', region: 'waw' },
};
