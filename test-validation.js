const BASE_URL = 'https://playground.4geeks.com/tracker/api/v1';

async function testInvalidStage() {
  try {
    console.log('--- Creando un record de prueba ---');
    const createRes = await fetch(`${BASE_URL}/records`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        full_name: 'Validation Test',
        email: `val_${Date.now()}@example.com`,
        phone: '123456789',
        position: 'Developer',
        experience_years: 3,
        status: 'applied',
        step: 'application',
        stage: 'screening'
      })
    });
    
    if (!createRes.ok) {
      console.log('No se pudo crear el record:', await createRes.text());
      return;
    }
    
    const record = await createRes.json();
    const id = record.id;
    console.log(`Usando record ID: ${id}`);

    console.log('\n--- Intentando PATCH con un stage INVÁLIDO ("invalid_stage_value") ---');
    const res = await fetch(`${BASE_URL}/records/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage: 'invalid_stage_value' })
    });
    
    console.log(`Status HTTP: ${res.status}`);
    const data = await res.json();
    console.log('Detalle de error 422:', JSON.stringify(data, null, 2));
    
  } catch (error) {
    console.error('Error:', error);
  }
}

testInvalidStage();
